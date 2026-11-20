"""Fusion add-in entry point and native command dialogs."""

from html import escape
from math import atan, cos, isfinite, pi, radians, tan
from pathlib import Path
import traceback

from adsk import core, fusion

from .gear_math import (DIN_FINE, ISO54_FIRST, ISO54_SECOND, ISO678_FIRST, ISO678_SECOND,
                        Gear, bevel_angle,
                        module_for_diameter, rack_outline)
from .model import Job, build

PANEL_ID = "ProGearDesignPanel"
WORKSPACE_ID = "FusionSolidEnvironment"
RESOURCE = Path(__file__).resolve().parent / "resources"

COMMANDS = (
    ("spur", "Spur gear", "spur"),
    ("helical", "Helical / herringbone gear", "helical"),
    ("bevel", "90° bevel pair", "bevel"),
    ("rack", "Straight / helical rack", "rack"),
    ("worm", "Worm drive", "worm"),
    ("ring", "Internal spur gear", "ring"),
    ("inverted", "Inverted internal spur gear", "inverted"),
    ("ring_helical", "Internal helical / herringbone gear", "ring_helical"),
    ("inverted_helical", "Inverted internal helical gear", "inverted_helical"),
    ("shifted", "Profile-shifted spur gear", "shifted"),
    ("shifted_helical", "Profile-shifted helical gear", "shifted_helical"),
)

_created_handlers = []
_dialogs = {}
_previous = {}


def application():
    return core.Application.get()


def log_error():
    application().log(traceback.format_exc())


def dropdown(inputs, key, label, choices, selected=0):
    field = inputs.addDropDownCommandInput(key, label, core.DropDownStyles.TextListDropDownStyle)
    for index, choice in enumerate(choices):
        field.listItems.add(choice, index == selected)
    return field


class Dialog:
    def __init__(self, command, kind, title):
        self.kind = kind
        self.title = title
        self.key = id(self)
        self.command = command
        self.inputs = {}
        self.handlers = []
        self.fit_on_preview = True
        self.internal = kind.startswith(("ring", "inverted"))
        self.is_worm = kind == "worm"
        self.is_bevel = kind == "bevel"
        self.is_rack = kind == "rack"
        self.is_helical = kind in ("helical", "ring_helical", "inverted_helical", "shifted_helical")
        self.preset_choices = [("DIN 780 · fine", m) for m in DIN_FINE] + [
            ("ISO 54 · I", m) for m in ISO54_FIRST
        ] + [("ISO 54 · II", m) for m in ISO54_SECOND]
        if self.is_bevel:
            self.preset_choices = [("ISO 678 · I", m) for m in ISO678_FIRST] + [
                ("ISO 678 · II", m) for m in ISO678_SECOND]
        if self.is_worm:
            self.preset_choices = [("Metric", m) for m in sorted(set(DIN_FINE + ISO54_FIRST))]

        command.isExecutedWhenPreEmpted = False
        command.setDialogInitialSize(440, 540)
        self.make_inputs(command.commandInputs)
        self.restore()
        self.update_visibility()
        self.attach(command.inputChanged, Changed)
        self.attach(command.validateInputs, Validate)
        self.attach(command.executePreview, Preview)
        self.attach(command.execute, Execute)
        self.attach(command.destroy, Destroy)

    def attach(self, event, event_type):
        handler = event_type(self)
        event.add(handler)
        self.handlers.append(handler)

    def make_inputs(self, inputs):
        fields = self.inputs

        def choice(group, key, label, options, selected=0):
            fields[key] = dropdown(group, key, label, options, selected)

        def length(group, key, label, mm):
            fields[key] = group.addValueInput(key, label, "mm", core.ValueInput.createByReal(mm / 10))

        def number(group, key, label, amount, units=""):
            value = core.ValueInput.createByString(f"{amount} {units}".strip())
            fields[key] = group.addValueInput(key, label, units, value)

        def checkbox(group, key, label, checked):
            fields[key] = group.addBoolValueInput(key, label, True, "", checked)

        modes = ["Manual module", "Metric presets", "Diametral pitch (teeth/in)"]
        if not self.is_rack:
            modes.extend(("Pitch diameter", "Tip diameter"))
        choice(inputs, "size", "Size by", modes, 1)
        choice(inputs, "preset", "Module preset",
               [f"m{m:g} mm · {series}" for series, m in self.preset_choices],
               next(i for i, (_, m) in enumerate(self.preset_choices) if m == 1))
        length(inputs, "module", "Module", 1)
        number(inputs, "dp", "Diametral pitch (teeth/in)", 25.4)
        length(inputs, "diameter", "Diameter", 48 if self.internal else 24)
        fields["teeth"] = inputs.addIntegerSpinnerCommandInput(
            "teeth", "Wheel teeth" if self.is_bevel else "Teeth", 6, 250, 1,
            48 if self.internal else 24)
        if self.is_bevel:
            fields["mate"] = inputs.addIntegerSpinnerCommandInput("mate", "Pinion teeth", 6, 250, 1, 18)
        if self.is_worm:
            choice(inputs, "worm_type", "Wheel tooth form", ("Helical", "Hobbed straight"))
            length(inputs, "worm_radius", "Worm core radius", 5)
            length(inputs, "worm_length", "Worm length", 20)
        if self.is_bevel or self.is_worm:
            checkbox(inputs, "grouped", "Put both gears in one parent component", True)

        width = "Face width on pitch cone" if self.is_bevel else "Face width"
        length(inputs, "width", width, 5 if self.is_bevel else 8)
        if self.internal:
            length(inputs, "rim", "Ring wall thickness", 5)
        if self.is_rack:
            length(inputs, "backing", "Rack base thickness", 5)
        number(inputs, "pressure", "Pressure / flank angle", 20, "deg")
        if self.is_helical or self.is_rack or self.is_worm:
            choice(inputs, "section", "Module and angle measured in", ("Normal plane", "Transverse plane"),
                   int(self.is_worm))
            checkbox(inputs, "left", "Left-hand helix", False)
            if not self.is_worm:
                number(inputs, "helix", "Helix angle", 15 if self.is_helical else 0, "deg")
            if self.is_helical:
                checkbox(inputs, "double", "Herringbone (double helix)", False)

        group = inputs.addGroupCommandInput("details", "Tooth profile and machining")
        group.isExpanded = self.kind.startswith("shifted")
        advanced = group.children
        number(advanced, "addendum", "Addendum (× module)", 1)
        number(advanced, "dedendum", "Dedendum (× module)", 1.25)
        length(advanced, "backlash", "Pitch-line tooth thinning", 0)
        if self.is_bevel:
            number(advanced, "clearance", "Pinion axial offset (× module)", 0.2)
        if self.is_worm:
            number(advanced, "clearance", "Center-distance increase (× module)", 0.15)
        if not (self.internal or self.is_bevel or self.is_rack or self.is_worm):
            number(advanced, "shift", "Profile shift coefficient", 0.2 if self.kind.startswith("shifted") else 0)
        if not (self.internal or self.is_rack):
            length(advanced, "bore", "Shaft bore diameter", 0)
        if self.is_bevel:
            length(advanced, "mate_bore", "Pinion bore diameter", 0)
        if not self.is_rack:
            length(advanced, "chamfer", "Tooth tip chamfer distance", 0)
            number(advanced, "chamfer_angle", "Tooth tip chamfer angle", 45, "deg")
        if not self.is_rack:
            checkbox(advanced, "fast", "Fast involute calculation", True)
        checkbox(inputs, "preview", "Live 3D preview", True)
        fields["status"] = inputs.addTextBoxCommandInput("status", "", "", 2, True)
        fields["status"].isFullWidth = True

    def restore(self):
        for key, saved in _previous.get(self.kind, {}).items():
            item = self.inputs[key]
            if hasattr(item, "listItems"):
                item.listItems.item(saved).isSelected = True
            elif hasattr(item, "expression"):
                item.expression = saved
            else:
                item.value = saved

    def remember(self):
        state = {}
        for key, item in self.inputs.items():
            if key == "status":
                continue
            if hasattr(item, "listItems"):
                state[key] = item.selectedItem.index
            elif hasattr(item, "expression"):
                state[key] = item.expression
            else:
                state[key] = item.value
        _previous[self.kind] = state

    def selected(self, key):
        return self.inputs[key].selectedItem.name

    def number(self, key, default=0):
        field = self.inputs.get(key)
        if field is None or not field.isVisible:
            return default
        if not getattr(field, "isValidExpression", True) or not isfinite(field.value):
            raise ValueError(f"Enter a valid value for {field.name}.")
        return field.value

    def mm(self, key, default=0):
        return self.number(key, default / 10) * 10

    def update_visibility(self):
        size = self.selected("size")
        for key, visible in (("preset", size == "Metric presets"), ("module", size == "Manual module"),
                             ("dp", size.startswith("Diametral")), ("diameter", size.endswith("diameter"))):
            if key in self.inputs:
                self.inputs[key].isVisible = visible
        if self.is_worm:
            hobbed = self.selected("worm_type") == "Hobbed straight"
            for key in ("section", "addendum", "dedendum", "bore", "chamfer", "chamfer_angle", "fast"):
                self.inputs[key].isVisible = not hobbed
        if self.is_rack:
            has_helix = self.number("helix") != 0
            self.inputs["section"].isVisible = has_helix
            self.inputs["left"].isVisible = has_helix

    def read_job(self):
        teeth, mate = int(self.number("teeth")), int(self.number("mate", 18))
        pressure = self.number("pressure")
        magnitude = self.number("helix")
        if not radians(10) - 1e-9 <= pressure <= radians(35) + 1e-9:
            raise ValueError("Pressure angle must be between 10° and 35°.")
        if not 0 <= magnitude < radians(89.5):
            raise ValueError("Helix angle must be between 0° and 89.5°.")
        hand = -1 if self.number("left") else 1
        helix = magnitude * hand
        cone = bevel_angle(teeth, mate) if self.is_bevel else 0
        hobbed = self.is_worm and self.selected("worm_type") == "Hobbed straight"
        normal = self.inputs["section"].selectedItem.index == 0 if "section" in self.inputs else True
        if hobbed:
            normal = False

        mode = self.selected("size")
        addendum, dedendum = self.number("addendum", 1), self.number("dedendum", 1.25)
        shift = self.number("shift")
        if min(addendum, dedendum) <= 0 or not -1 <= shift <= 1 or not -addendum < shift < dedendum:
            raise ValueError("Use positive tooth heights and a profile shift between −1 and 1.")
        if mode == "Metric presets":
            module = self.preset_choices[self.inputs["preset"].selectedItem.index][1]
            if not self.is_worm and not normal and helix:
                module /= cos(helix)
        elif mode == "Manual module":
            module = self.mm("module")
        elif mode.startswith("Diametral"):
            pitch = self.number("dp")
            if pitch <= 0:
                raise ValueError("Diametral pitch must be positive.")
            module = 25.4 / pitch
        else:
            diameter = self.mm("diameter")
            # In normal-system worm mode the lead angle depends on the module.
            if self.is_worm and normal:
                low, high = 0.0, module_for_diameter(
                    diameter, teeth, mode, addendum=addendum, internal=self.internal)
                for _ in range(50):
                    trial = (low + high) / 2
                    trial_helix = self.worm_helix(trial)
                    result = module_for_diameter(diameter, teeth, mode, trial_helix, True, addendum)
                    if trial < result:
                        low = trial
                    else:
                        high = trial
                module = (low + high) / 2
            else:
                module = module_for_diameter(diameter, teeth, mode, helix, normal,
                                             addendum, shift, self.internal, cone)
        if module <= 0:
            raise ValueError("Module or diameter must be positive.")
        if self.is_worm:
            helix = hand * self.worm_helix(module)
        gear = Gear(module, teeth, pressure, helix, normal, addendum, dedendum, shift,
                    self.mm("backlash"), self.internal, self.kind.startswith("inverted"), cone)
        kind = ("bevel" if self.is_bevel else "rack" if self.is_rack else
                "hobbed" if hobbed else "worm" if self.is_worm else "cylinder")
        job = Job(kind, gear, self.mm("width"), mate, self.mm("rim", 5), self.mm("backing", 5),
                  self.mm("bore"), self.mm("mate_bore"), self.mm("worm_radius", 5),
                  self.mm("worm_length", 20), bool(self.number("grouped")),
                  bool(self.number("double")), 12 if self.number("fast") else 32,
                  self.mm("chamfer"), self.number("chamfer_angle", pi / 4), self.number("clearance") * module)
        self.check_job(job)
        return job

    def worm_helix(self, module):
        radius = self.mm("worm_radius")
        if radius <= 0:
            raise ValueError("Worm core radius must be positive.")
        return atan(module / (2 * (radius + 1.25 * module)))

    @staticmethod
    def check_job(job):
        g = job.gear
        if job.width <= 0 or job.rim <= 0 or job.backing <= 0:
            raise ValueError("Face width, ring wall and rack base must be positive.")
        if min(g.backlash, job.bore, job.mate_bore, job.chamfer, job.clearance) < 0:
            raise ValueError("Tooth thinning, bores, chamfer and clearance must be zero or positive.")
        if job.chamfer and not (0 < job.chamfer_angle < pi / 2 and 2 * job.chamfer < job.width):
            raise ValueError("Use a chamfer angle between 0° and 90° and a smaller chamfer distance.")
        if job.kind == "rack":
            rack_outline(g.teeth, g.transverse_module, g.transverse_pressure, job.backing,
                         g.addendum * g.module, g.dedendum * g.module, g.backlash)
        else:
            g.sector(4)
        if job.kind == "bevel":
            job.mate.sector(4)
            cone_distance = (g.pitch ** 2 + job.mate.pitch ** 2) ** 0.5
            if job.width >= cone_distance:
                raise ValueError("Bevel face width must be less than the pitch cone distance.")
            scale = 1 - job.width / cone_distance
            if job.bore / 2 >= g.root * scale or job.mate_bore / 2 >= job.mate.root * scale:
                raise ValueError("Each bore must fit within the small-end root diameter.")
        elif job.kind != "rack" and not g.internal and job.bore / 2 >= g.root:
            raise ValueError("Bore must fit inside the root diameter.")
        if job.kind in ("worm", "hobbed"):
            if job.worm_length < pi * g.module:
                raise ValueError("Worm length must be at least one axial pitch (π × module).")
            if job.worm_radius <= 0.05 * g.module:
                raise ValueError("Worm core radius is too small for this module.")

    def status(self, text):
        item = self.inputs["status"]
        shown = escape(text)
        if item.formattedText != shown:
            item.formattedText = shown

    def target(self, job):
        design = fusion.Design.cast(application().activeProduct)
        if not design:
            raise ValueError("Open a Fusion Design document first.")
        intent = getattr(design, "designIntent", None)
        intents = getattr(fusion, "DesignIntentTypes", None)
        if intents and (intent == intents.AssemblyDesignIntentType or
                        (job.pair and intent == intents.PartDesignIntentType)):
            raise ValueError("Use a Hybrid Design to create gear components in this document.")
        if design.designType != fusion.DesignTypes.ParametricDesignType:
            raise ValueError("Use a design with Capture Design History enabled.")
        return design.activeComponent, bool(intents and intent == intents.PartDesignIntentType)

    def generate(self, args, preview):
        if preview and not self.inputs["preview"].value:
            return
        try:
            job = self.read_job()
            parent, in_place = self.target(job)
            name = f"{self.title} · m{job.gear.module:.4g} · {job.gear.teeth}T"
            fit = preview and self.fit_on_preview and not (
                parent.bRepBodies.count or parent.occurrences.count)
            build(parent, job, name, in_place=in_place)
            if preview:
                args.isValidResult = True
                self.fit_on_preview = False
                if fit:
                    application().activeViewport.fit()
        except ValueError as exc:
            self.status(str(exc))
            if not preview:
                args.executeFailedMessage = str(exc)
                args.executeFailed = True
        except Exception as exc:
            log_error()
            error = f"Fusion could not build this geometry. {exc}"
            self.status(error)
            if not preview:
                args.executeFailedMessage = error
                args.executeFailed = True


class Changed(core.InputChangedEventHandler):
    def __init__(self, dialog):
        super().__init__()
        self.dialog = dialog

    def notify(self, args):
        if args.input.id != "status":
            try:
                self.dialog.update_visibility()
            except ValueError:
                pass


class Validate(core.ValidateInputsEventHandler):
    def __init__(self, dialog):
        super().__init__()
        self.dialog = dialog

    def notify(self, args):
        try:
            job = self.dialog.read_job()
            self.dialog.target(job)
            g = job.gear
            if job.kind == "rack":
                self.dialog.status(f"Tooth pitch {pi * g.transverse_module:.4g} mm")
            else:
                self.dialog.status(f"m {g.module:.4g} mm    •    Pitch Ø {2*g.pitch:.4g} mm"
                                   f"    •    Tip Ø {2*g.tip:.4g} mm")
            args.areInputsValid = True
        except ValueError as exc:
            self.dialog.status(str(exc))
            args.areInputsValid = False
        except Exception:
            args.areInputsValid = False
            log_error()


class Preview(core.CommandEventHandler):
    def __init__(self, dialog):
        super().__init__()
        self.dialog = dialog

    def notify(self, args):
        self.dialog.generate(args, preview=True)


class Execute(core.CommandEventHandler):
    def __init__(self, dialog):
        super().__init__()
        self.dialog = dialog

    def notify(self, args):
        self.dialog.generate(args, preview=False)


class Destroy(core.CommandEventHandler):
    def __init__(self, dialog):
        super().__init__()
        self.dialog = dialog

    def notify(self, args):
        try:
            if args.terminationReason == core.CommandTerminationReason.CompletedTerminationReason:
                self.dialog.remember()
        finally:
            _dialogs.pop(self.dialog.key, None)


class Created(core.CommandCreatedEventHandler):
    def __init__(self, kind, title):
        super().__init__()
        self.kind = kind
        self.title = title

    def notify(self, args):
        try:
            dialog = Dialog(args.command, self.kind, self.title)
            _dialogs[dialog.key] = dialog
        except Exception:
            log_error()


def run(context):
    ui = application().userInterface
    try:
        stop(context)
        workspace = ui.workspaces.itemById(WORKSPACE_ID)
        panel = workspace.toolbarPanels.add(PANEL_ID, "ProGear", "SelectPanel", False)
        for kind, title, icon in COMMANDS:
            command = ui.commandDefinitions.addButtonDefinition(
                f"ProGear_{kind}", title, f"Generate {title.lower()}", str(RESOURCE / icon))
            handler = Created(kind, title)
            command.commandCreated.add(handler)
            _created_handlers.append(handler)
            control = panel.controls.addCommand(command)
            if kind in ("spur", "helical", "bevel"):
                control.isPromotedByDefault = True
                control.isPromoted = True
    except Exception:
        ui.messageBox(f"ProGear could not start.\n{traceback.format_exc()}")


def stop(context):
    ui = application().userInterface
    workspace = ui.workspaces.itemById(WORKSPACE_ID)
    if workspace:
        panel = workspace.toolbarPanels.itemById(PANEL_ID)
        if panel:
            panel.deleteMe()
    for kind, _, _ in COMMANDS:
        cmd = ui.commandDefinitions.itemById(f"ProGear_{kind}")
        if cmd:
            cmd.deleteMe()
    _created_handlers.clear()
