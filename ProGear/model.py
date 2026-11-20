"""Build native Fusion features from the shared geometry."""

from dataclasses import dataclass
from math import cos, degrees, pi, tan

from adsk import core, fusion

from .gear_math import Gear, bevel_angle, polar, rack_outline

NEW = fusion.FeatureOperations.NewBodyFeatureOperation
JOIN = fusion.FeatureOperations.JoinFeatureOperation
CUT = fusion.FeatureOperations.CutFeatureOperation


@dataclass(frozen=True)
class Job:
    kind: str
    gear: Gear
    width: float
    mate_teeth: int = 18
    rim: float = 5.0
    backing: float = 5.0
    bore: float = 0.0
    mate_bore: float = 0.0
    worm_radius: float = 5.0
    worm_length: float = 20.0
    grouped: bool = True
    double: bool = False
    quality: int = 12
    chamfer: float = 0.0
    chamfer_angle: float = pi / 4
    clearance: float = 0.0

    @property
    def pair(self):
        return self.kind in ("bevel", "worm", "hobbed")

    @property
    def mate(self):
        g = self.gear
        return Gear(g.module, self.mate_teeth, g.pressure, addendum=g.addendum,
                    dedendum=g.dedendum, backlash=g.backlash,
                    cone=bevel_angle(self.mate_teeth, g.teeth))


def point(x=0.0, y=0.0, z=0.0):
    return core.Point3D.create(x / 10, y / 10, z / 10)


def value(mm):
    return core.ValueInput.createByReal(mm / 10)


def collection(items):
    result = core.ObjectCollection.create()
    for item in items:
        result.add(item)
    return result


def component(parent, name, matrix=None):
    occ = parent.occurrences.addNewComponent(matrix or core.Matrix3D.create())
    occ.component.name = name
    return occ.component


def offset_sketch(comp, z, name):
    plane = comp.xYConstructionPlane
    if z:
        plane_input = comp.constructionPlanes.createInput()
        plane_input.setByOffset(plane, value(z))
        plane = comp.constructionPlanes.add(plane_input)
        plane.isLightBulbOn = False
    sketch = comp.sketches.add(plane)
    sketch.name = name
    return sketch


def circular_sketch(comp, gear, z, samples, scale=1, phase=0, rim=0):
    sketch = offset_sketch(comp, z, "Tooth profile")
    sketch.isComputeDeferred = True
    curves = sketch.sketchCurves
    extension, flank, upper_half = gear.sector(samples)
    low, high = sorted((gear.root, gear.tip))
    low_half = (extension or flank)[0][1]
    pitch = 2 * pi / gear.teeth

    def at(radius, angle):
        return point(*polar(radius * scale, angle))

    for tooth in range(gear.teeth):
        angle = phase + tooth * pitch
        for side in (-1, 1):
            if extension:
                points = [at(r, angle + side * a) for r, a in extension]
                curves.sketchLines.addByTwoPoints(*points)
            points = [at(r, angle + side * a) for r, a in flank]
            curves.sketchFittedSplines.add(collection(points))
        curves.sketchArcs.addByCenterStartSweep(point(), at(high, angle - upper_half), 2 * upper_half)
        curves.sketchArcs.addByCenterStartSweep(point(), at(low, angle + low_half), pitch - 2 * low_half)
    if rim:
        curves.sketchCircles.addByCenterRadius(point(), (gear.root + rim) * scale / 10)
    sketch.isComputeDeferred = False
    return sketch


def profile(sketch, annulus=False):
    for candidate in sketch.profiles:
        if candidate.profileLoops.count == (2 if annulus else 1):
            return candidate
    raise RuntimeError(f"Fusion did not close the profile in {sketch.name}.")


def extrude(comp, section, distance, operation=NEW, target=None):
    features = comp.features.extrudeFeatures
    info = features.createInput(section, operation)
    extent = fusion.DistanceExtentDefinition.create(value(abs(distance)))
    direction = (fusion.ExtentDirections.PositiveExtentDirection if distance > 0
                 else fusion.ExtentDirections.NegativeExtentDirection)
    info.setOneSideExtent(extent, direction)
    if target:
        info.participantBodies = [target]
    return features.add(info)


def loft(comp, sections):
    features = comp.features.loftFeatures
    info = features.createInput(NEW)
    for section in sections:
        info.loftSections.add(section)
    return features.add(info)


def twist_sweep(comp, section, start, width, twist, operation):
    path_sketch = offset_sketch(comp, start, "Helix axis")
    line = path_sketch.sketchCurves.sketchLines.addByTwoPoints(point(), point(z=width))
    path = comp.features.createPath(line, False)
    sweeps = comp.features.sweepFeatures
    info = sweeps.createInput(section, path, operation)
    info.orientation = fusion.SweepOrientationTypes.PerpendicularOrientationType
    info.twistAngle = core.ValueInput.createByReal(twist)
    feature = sweeps.add(info)
    path_sketch.isVisible = False
    return feature


def cylinder(comp, gear, job, phase=0):
    section = circular_sketch(comp, gear, 0, job.quality, phase=phase,
                              rim=job.rim if gear.internal else 0)
    start = profile(section, gear.internal)
    width = job.width
    if not gear.helix:
        feature = extrude(comp, start, width)
    elif not job.double:
        feature = twist_sweep(comp, start, 0, width, gear.twist_per_mm * width, NEW)
    else:
        # Both halves grow outward from the shared section, with the same phase at the ends.
        half = width / 2
        twist = gear.twist_per_mm * half
        feature = twist_sweep(comp, start, 0, -half, -twist, NEW)
        second = twist_sweep(comp, start, 0, half, twist, NEW)
        feature = combine(comp, feature.bodies.item(0), [second.bodies.item(0)])
    section.isVisible = False
    return feature.bodies.item(0), (-width / 2 if job.double and gear.helix else 0)


def combine(comp, target, tools):
    features = comp.features.combineFeatures
    info = features.createInput(target, collection(tools))
    info.operation = JOIN
    return features.add(info)


def rack(comp, job):
    g = job.gear
    module = g.module
    outline = rack_outline(g.teeth, g.transverse_module, g.transverse_pressure,
                           job.backing, g.addendum * module, g.dedendum * module, g.backlash)
    offsets = (0, job.width) if g.helix else (0,)
    sketches = []
    for z in offsets:
        sketch = offset_sketch(comp, z, "Rack profile")
        sketch.isComputeDeferred = True
        lines = sketch.sketchCurves.sketchLines
        dx = z * tan(g.helix)
        vertices = [point(x + dx, y) for x, y in outline]
        for start, end in zip(vertices, vertices[1:] + vertices[:1]):
            lines.addByTwoPoints(start, end)
        sketch.isComputeDeferred = False
        sketches.append(sketch)
    sections = [profile(section) for section in sketches]
    if g.helix:
        feature = loft(comp, sections)
    else:
        feature = extrude(comp, sections[0], job.width)
    for sketch in sketches:
        sketch.isVisible = False
    return feature.bodies.item(0)


def bevel(comp, gear, job, mate_pitch, phase, bore_size):
    # The two axial sections share a pitch-cone apex at the component origin.
    far = mate_pitch
    near = far - job.width * cos(gear.cone)
    outer = circular_sketch(comp, gear, far, job.quality, phase=phase)
    inner = circular_sketch(comp, gear, near, job.quality, near / far, phase)
    feature = loft(comp, (profile(inner), profile(outer)))
    outer.isVisible = inner.isVisible = False
    body = feature.bodies.item(0)
    finish(comp, body, near, far - near, bore_size, job, gear, near / far)


def finish(comp, body, start, width, bore_size, job, gear, near_scale=1):
    if bore_size:
        margin = max(width * 0.01, 0.01)
        sketch = offset_sketch(comp, start - margin, "Shaft bore")
        sketch.sketchCurves.sketchCircles.addByCenterRadius(point(), bore_size / 20)
        extrude(comp, profile(sketch), width + 2 * margin, CUT, body)
        sketch.isVisible = False
    if job.chamfer:
        tip_chamfer(comp, body, start, width, gear, near_scale, job)


def tip_chamfer(comp, body, start, width, gear, near_scale, job):
    """Remove a conical relief from the tips at each end of the tooth face."""
    distance = job.chamfer
    depth = distance * tan(job.chamfer_angle)
    if 2 * depth >= width or distance >= abs(gear.tip - gear.root) * near_scale:
        raise ValueError("The tip chamfer must fit within the face width and tooth height.")
    radial_side = -1 if gear.internal else 1
    slope = gear.tip * (1 - near_scale) / width
    eps = min(depth / 10, distance / 10, 0.01)
    revolves = comp.features.revolveFeatures
    for end, radius, side in ((start, gear.tip * near_scale, -1), (start + width, gear.tip, 1)):
        inside = end - side * depth
        outside = end + side * eps
        inner_tip = radius + slope * (inside - end)
        outer_tip = radius + slope * (outside - end)
        outer_relief = distance * (1 + eps / depth)
        clearance = radial_side * max(distance, abs(slope) * (depth + eps), 0.1) * 2
        vertices = ((inner_tip, inside), (outer_tip - radial_side * outer_relief, outside),
                    (outer_tip + clearance, outside), (inner_tip + clearance, inside))
        sketch = comp.sketches.add(comp.xZConstructionPlane)
        sketch.name = "Tooth tip chamfer"
        points = [sketch.modelToSketchSpace(point(r, 0, z)) for r, z in vertices]
        lines = sketch.sketchCurves.sketchLines
        for first, second in zip(points, points[1:] + points[:1]):
            lines.addByTwoPoints(first, second)
        info = revolves.createInput(profile(sketch), comp.zConstructionAxis, CUT)
        info.participantBodies = [body]
        info.setAngleExtent(False, core.ValueInput.createByReal(2 * pi))
        revolves.add(info)
        sketch.isVisible = False


def pair_matrix(offset):
    result = core.Matrix3D.create()
    result.setWithCoordinateSystem(point(x=offset), core.Vector3D.create(0, 0, 1),
                                   core.Vector3D.create(0, -1, 0), core.Vector3D.create(1, 0, 0))
    return result


def translated(x=0, y=0, z=0):
    result = core.Matrix3D.create()
    result.translation = core.Vector3D.create(x / 10, y / 10, z / 10)
    return result


def build(parent, job, name, *, in_place=False):
    g = job.gear
    if job.pair and job.grouped:
        parent = component(parent, name)

    if job.kind == "bevel":
        wheel = component(parent, f"Bevel wheel · {g.teeth}T")
        pinion = component(parent, f"Bevel pinion · {job.mate_teeth}T", pair_matrix(job.clearance))
        bevel(wheel, g, job, job.mate.pitch, 0, job.bore)
        bevel(pinion, job.mate, job, g.pitch, pi / job.mate_teeth, job.mate_bore)
        output = parent if job.grouped else wheel
    elif job.kind in ("worm", "hobbed"):
        from . import worm

        wheel = worm.build(parent, job)
        output = parent if job.grouped else wheel
    else:
        output = parent if in_place else component(parent, name)
        if job.kind == "rack":
            body = rack(output, job)
        else:
            body, start = cylinder(output, g, job)
            finish(output, body, start, job.width, job.bore, job, g)
        body.name = name
    add_specification(output, job, name)


def add_specification(comp, job, name):
    g = job.gear
    rows = [
        name,
        f"Module {g.module:.5g} mm | Teeth {g.teeth} | Face width {job.width:.5g} mm",
        f"Pressure angle {degrees(g.pressure):.4g} deg | Helix angle {degrees(g.helix):.4g} deg",
        f"Addendum {g.addendum:.4g} m | Dedendum {g.dedendum:.4g} m | Profile shift {g.shift:.4g}",
    ]
    if job.kind == "rack":
        rows.append(f"Pitch {pi * g.transverse_module:.5g} mm | Base {job.backing:.5g} mm")
    else:
        rows.append(f"Pitch diameter {2 * g.pitch:.5g} mm | Tip diameter {2 * g.tip:.5g} mm")
    if job.kind == "bevel":
        rows.append(f"Pinion {job.mate_teeth} teeth | Axial offset {job.clearance:.5g} mm")
    if job.kind in ("worm", "hobbed"):
        rows.append(f"Worm length {job.worm_length:.5g} mm | Core radius {job.worm_radius:.5g} mm")
    sketch = offset_sketch(comp, 0, "Gear specification")
    texts = sketch.sketchTexts
    text = texts.createInput2("\n".join(rows), 0.22)
    text.setAsMultiLine(point(), point(160, 25), core.HorizontalAlignments.LeftHorizontalAlignment,
                        core.VerticalAlignments.TopVerticalAlignment, 0)
    texts.add(text)
    sketch.isVisible = False
