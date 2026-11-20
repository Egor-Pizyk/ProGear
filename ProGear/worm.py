"""Worm thread and hobbing geometry adapted from GF Gear Generator.

Copyright (c) 2022, CenturySturgeon. See LICENSE.
"""

from math import ceil, floor, pi, tan

from adsk import core, fusion

from .model import (CUT, NEW, collection, combine, component, cylinder,
                    extrude, finish, point, profile, translated, value)


def thread_sections(comp, gear, core_radius, allowance):
    """Build three trapezoids at half-pitch steps on opposite sides of the worm."""
    module = gear.module
    pitch = pi * module
    pressure_slope = tan(gear.pressure)
    radius = core_radius + 1.25 * module
    tip_height = module + allowance / 2
    tip_radius = radius + tip_height
    # Extend the root slightly into the core so the lofts can be joined.
    tool_depth = 1.30 * module
    root_radius = radius - tool_depth
    upper = pitch / 4 - tip_height * pressure_slope + allowance
    lower = pitch / 4 + tool_depth * pressure_slope + allowance
    sections = []
    for index, direction in enumerate((1, -1, 1)):
        x = index * pitch / 2
        tip, root = direction * tip_radius, direction * root_radius
        vertices = [point(x - upper, tip), point(x + upper, tip),
                    point(x + lower, root), point(x - lower, root)]
        sketch = comp.sketches.add(comp.xYConstructionPlane)
        sketch.name = "Worm tooth section"
        lines = sketch.sketchCurves.sketchLines
        for first, second in zip(vertices, vertices[1:] + vertices[:1]):
            lines.addByTwoPoints(first, second)
        sections.append(profile(sketch))
        sketch.isVisible = False
    return sections


def core_sketch(comp, radius, name, x=0):
    plane = comp.yZConstructionPlane
    if x:
        info = comp.constructionPlanes.createInput()
        info.setByOffset(plane, value(x))
        plane = comp.constructionPlanes.add(info)
        plane.isLightBulbOn = False
    sketch = comp.sketches.add(plane)
    sketch.name = name
    circle = sketch.sketchCurves.sketchCircles.addByCenterRadius(point(), radius / 10)
    return sketch, circle


def thread_turn(comp, job, circle, allowance=0):
    first, middle, last = thread_sections(comp, job.gear, job.worm_radius, allowance)
    halves = ((first, middle), (middle, last))
    if job.gear.helix < 0:
        halves = ((middle, first), (last, middle))
    path = comp.features.createPath(circle, False)
    features = comp.features.loftFeatures
    threads = []
    for pair in halves:
        info = features.createInput(NEW)
        for section in pair:
            info.loftSections.add(section)
        info.centerLineOrRails.addCenterLine(path)
        threads.append(features.add(info))
    return threads


def screw(comp, job, pitch):
    base, _ = core_sketch(comp, job.worm_radius, "Worm core", -pitch)
    blank = extrude(comp, profile(base), job.worm_length + 4 * pitch).bodies.item(0)
    guide, edge = core_sketch(comp, job.worm_radius, "Worm path")
    turns = thread_turn(comp, job, edge)
    patterns = comp.features.rectangularPatternFeatures
    repeats = core.ValueInput.createByReal(ceil(job.worm_length / pitch) + 1)
    for turn in turns:
        info = patterns.createInput(collection([turn]), comp.xConstructionAxis, repeats,
                                    value(pitch), fusion.PatternDistanceType.SpacingPatternDistanceType)
        patterns.add(info)
    tools = [comp.bRepBodies.item(i) for i in range(1, comp.bRepBodies.count)]
    body = combine(comp, blank, tools).bodies.item(0)
    trim_radius = job.worm_radius + 3 * job.gear.module
    for axial, direction in ((0, -1), (job.worm_length, 1)):
        trim, _ = core_sketch(comp, trim_radius, "Worm end trim", axial)
        extrude(comp, profile(trim), direction * 4 * pitch, CUT, body)
        trim.isVisible = False
    base.isVisible = guide.isVisible = False


def hobbed_wheel(comp, job, pitch, distance):
    base = comp.sketches.add(comp.xYConstructionPlane)
    base.name = "Worm wheel blank"
    x, y = pitch / 2, -distance
    center = point(x, y)
    axis = base.sketchCurves.sketchLines.addByTwoPoints(center, point(x, y, 10))
    base.sketchCurves.sketchCircles.addByCenterRadius(center, job.gear.tip / 10)
    extrudes = comp.features.extrudeFeatures
    info = extrudes.createInput(profile(base), NEW)
    info.setSymmetricExtent(value(job.width), True)
    blank = extrudes.add(info).bodies.item(0)

    path_sketch, guide = core_sketch(comp, job.worm_radius, "Hob path")
    # The hob is wider than the mating screw to allow it to turn after cutting.
    allowance = 0.20 * job.gear.module + job.gear.backlash / 2
    tools = [turn.bodies.item(0) for turn in thread_turn(comp, job, guide, allowance)]
    patterns = comp.features.circularPatternFeatures
    info = patterns.createInput(collection(tools), axis)
    info.quantity = core.ValueInput.createByReal(job.gear.teeth)
    patterns.add(info)

    combines = comp.features.combineFeatures
    cutters = [comp.bRepBodies.item(i) for i in range(1, comp.bRepBodies.count)]
    cut = combines.createInput(blank, collection(cutters))
    cut.operation = CUT
    combines.add(cut)
    base.isVisible = path_sketch.isVisible = False


def build(parent, job):
    g = job.gear
    pitch = pi * g.module
    station = (floor(job.worm_length / (2 * pitch)) + 0.5) * pitch
    distance = g.pitch + job.worm_radius + 1.25 * g.module
    shaft = component(parent, "Worm screw",
                      translated(-station, distance + job.clearance, job.width / 2))
    screw(shaft, job, pitch)

    if job.kind == "hobbed":
        wheel = component(parent, f"Hobbed worm wheel · {g.teeth}T",
                          translated(-pitch / 2, distance, job.width / 2))
        hobbed_wheel(wheel, job, pitch, distance)
    else:
        wheel = component(parent, f"Helical worm wheel · {g.teeth}T")
        phase = pi / 2 - pi / g.teeth - g.twist_per_mm * job.width / 2
        body, start = cylinder(wheel, g, job, phase)
        finish(wheel, body, start, job.width, job.bore, job, g)
    return wheel
