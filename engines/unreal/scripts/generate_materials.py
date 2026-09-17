#!/usr/bin/env python
"""
generate_materials.py -- build the five AetherFX materials as .uasset files.

Runs inside the Unreal editor's Python interpreter (PythonScriptPlugin), in a
commandlet or from the editor's Python console:

    "/Users/Shared/Epic Games/UE_5.8/Engine/Binaries/Mac/UnrealEditor-Cmd" \
        engines/unreal/TestProject/AetherFXTest.uproject \
        -run=pythonscript -script="engines/unreal/scripts/generate_materials.py" \
        -unattended -nopause -nullrhi -NoSound -stdout

It writes into the plugin's own content folder, /AetherFX/Materials, so the
generated assets ship with the plugin:

    M_AetherFX_Billboard_Additive     unlit, additive, per-instance custom data
    M_AetherFX_Billboard_Translucent  unlit, translucent, per-instance custom data
    M_AetherFX_Mesh                   lit, emissive + Fresnel rim
    M_AetherFX_Decal                  deferred decal
    M_AetherFX_Ribbon                 unlit, premultiplied; trails and beams

Billboards are *not* oriented in the shader: UAetherFXComponent builds the
camera-facing instance transform on the CPU each tick, so these materials only
have to shade a quad. That keeps the graphs small enough to generate reliably
headless, which is the whole point of this script.

Per-instance custom data layout (must match AetherFX::CustomData_* in
AetherFXTypes.h):

    0,1,2  linear RGB tint          5,6  sub-UV offset (u0, v0)
    3      alpha                    7,8  sub-UV scale  (du, dv)
    4      per-particle emissive    9    normalised age
"""

import unreal

MATERIAL_PATH = "/AetherFX/Materials"

CD_COLOR = 0
CD_ALPHA = 3
CD_EMISSIVE = 4
CD_UV_OFFSET_U = 5
CD_UV_OFFSET_V = 6
CD_UV_SCALE_U = 7
CD_UV_SCALE_V = 8
CD_AGE = 9

WHITE_TEXTURE = "/Engine/EngineResources/WhiteSquareTexture.WhiteSquareTexture"

MEL = unreal.MaterialEditingLibrary

_failures = []


def log(message):
    unreal.log("[AetherFX materials] {}".format(message))


def fail(message):
    _failures.append(message)
    unreal.log_error("[AetherFX materials] {}".format(message))


# ---------------------------------------------------------------------------
# graph helpers
# ---------------------------------------------------------------------------

class Builder(object):
    """Thin wrapper over UMaterialEditingLibrary with node auto-layout."""

    def __init__(self, material):
        self.material = material
        self._column = 0
        self._row = 0

    def _next_position(self):
        x = -1900 + (self._column * 240)
        y = -600 + (self._row * 140)
        self._row += 1
        if self._row > 10:
            self._row = 0
            self._column += 1
        return x, y

    def node(self, expression_class, **properties):
        x, y = self._next_position()
        expression = MEL.create_material_expression(self.material, expression_class, x, y)
        if expression is None:
            fail("could not create {}".format(expression_class))
            return None
        for name, value in properties.items():
            try:
                expression.set_editor_property(name, value)
            except Exception as error:      # noqa: BLE001 - report and carry on
                fail("{}.{} = {!r}: {}".format(expression_class, name, value, error))
        return expression

    def link(self, source, source_output, target, target_input):
        if source is None or target is None:
            return
        if not MEL.connect_material_expressions(source, source_output, target, target_input):
            fail("cannot connect {} '{}' -> {} '{}'".format(
                source.get_class().get_name(), source_output,
                target.get_class().get_name(), target_input))

    def to_property(self, source, source_output, material_property):
        if source is None:
            return
        if not MEL.connect_material_property(source, source_output, material_property):
            fail("cannot connect {} '{}' -> {}".format(
                source.get_class().get_name(), source_output, material_property))

    # -- composite nodes ----------------------------------------------------

    def multiply(self, a, a_out, b, b_out):
        node = self.node(unreal.MaterialExpressionMultiply)
        self.link(a, a_out, node, "A")
        self.link(b, b_out, node, "B")
        return node

    def add(self, a, a_out, b, b_out):
        node = self.node(unreal.MaterialExpressionAdd)
        self.link(a, a_out, node, "A")
        self.link(b, b_out, node, "B")
        return node

    def append(self, a, a_out, b, b_out):
        node = self.node(unreal.MaterialExpressionAppendVector)
        self.link(a, a_out, node, "A")
        self.link(b, b_out, node, "B")
        return node

    def custom_data(self, index, default=0.0):
        return self.node(
            unreal.MaterialExpressionPerInstanceCustomData,
            data_index=index,
            const_default_value=default,
        )

    def custom_data_rgb(self, index, default=unreal.LinearColor(1.0, 1.0, 1.0, 1.0)):
        return self.node(
            unreal.MaterialExpressionPerInstanceCustomData3Vector,
            data_index=index,
            const_default_value=default,
        )

    def scalar(self, name, default, group="AetherFX"):
        return self.node(
            unreal.MaterialExpressionScalarParameter,
            parameter_name=name,
            default_value=default,
            group=group,
        )

    def vector(self, name, default, group="AetherFX"):
        return self.node(
            unreal.MaterialExpressionVectorParameter,
            parameter_name=name,
            default_value=default,
            group=group,
        )

    def texture(self, name, group="AetherFX"):
        white = unreal.EditorAssetLibrary.load_asset(WHITE_TEXTURE)
        node = self.node(
            unreal.MaterialExpressionTextureSampleParameter2D,
            parameter_name=name,
            group=group,
        )
        if node is not None and white is not None:
            node.set_editor_property("texture", white)
        return node

    def mask(self, source, source_output="", r=False, g=False, b=False, a=False):
        """ComponentMask. Its single input is unnamed, so "" selects it."""
        node = self.node(unreal.MaterialExpressionComponentMask, r=r, g=g, b=b, a=a)
        self.link(source, source_output, node, "")
        return node

    def constant(self, value):
        return self.node(unreal.MaterialExpressionConstant, r=value)

    def sub_uv(self):
        """TexCoord0 * (scale u, scale v) + (offset u, offset v)."""
        coords = self.node(unreal.MaterialExpressionTextureCoordinate, coordinate_index=0)
        scale = self.append(
            self.custom_data(CD_UV_SCALE_U, 1.0), "",
            self.custom_data(CD_UV_SCALE_V, 1.0), "")
        offset = self.append(
            self.custom_data(CD_UV_OFFSET_U, 0.0), "",
            self.custom_data(CD_UV_OFFSET_V, 0.0), "")
        return self.add(self.multiply(coords, "", scale, ""), "", offset, "")


def create_material(name):
    """
    Returns an empty UMaterial at /AetherFX/Materials/<name>.

    Re-running the script must be safe, and `create_asset` refuses to overwrite
    while unattended, so an existing material is emptied and rebuilt in place --
    which also keeps its GUID, so material instances survive a regeneration.
    """
    package = "{}/{}".format(MATERIAL_PATH, name)

    # In a commandlet the asset registry has not necessarily finished its first
    # scan when this runs, so ask it for this folder explicitly -- otherwise a
    # second run does not see the assets the first run wrote and create_asset
    # refuses to overwrite them while unattended.
    registry = unreal.AssetRegistryHelpers.get_asset_registry()
    registry.scan_paths_synchronous([MATERIAL_PATH], True)

    if unreal.EditorAssetLibrary.does_asset_exist(package):
        existing = unreal.EditorAssetLibrary.load_asset(package)
        material = existing if isinstance(existing, unreal.Material) else None
        if material is not None:
            MEL.delete_all_material_expressions(material)
            log("rebuilding existing {}".format(package))
            return material
        fail("'{}' exists but is not a Material".format(package))
        return None

    tools = unreal.AssetToolsHelpers.get_asset_tools()
    material = tools.create_asset(name, MATERIAL_PATH, unreal.Material, unreal.MaterialFactoryNew())
    if material is None:
        fail("could not create material '{}'".format(package))
    return material


def allow_instanced_static_meshes(material):
    # ISM/HISM draws need the usage flag or the material falls back to default.
    try:
        MEL.set_base_material_usage(material, unreal.MaterialUsage.MATUSAGE_INSTANCED_STATIC_MESHES, True)
    except Exception as error:      # noqa: BLE001
        fail("set_base_material_usage: {}".format(error))


# ---------------------------------------------------------------------------
# the five materials
# ---------------------------------------------------------------------------

def build_billboard(name, additive):
    """
    Unlit sprite quad.

      colour   = instanceRGB * BaseColor * texRGB
      emission = colour * EmissiveColor * (instanceEmissive + EmissiveIntensity)
      alpha    = texA * instanceAlpha * Opacity

    Additive: UE ignores the Opacity input, so alpha is folded into emissive
    (`src *= a`, exactly what docs/ENGINE_INTEGRATION.md 5 prescribes).
    Translucent: emissive carries the colour and alpha drives Opacity.
    """
    material = create_material(name)
    if material is None:
        return None

    material.set_editor_property("material_domain", unreal.MaterialDomain.MD_SURFACE)
    material.set_editor_property("shading_model", unreal.MaterialShadingModel.MSM_UNLIT)
    material.set_editor_property("blend_mode",
                                 unreal.BlendMode.BLEND_ADDITIVE if additive else unreal.BlendMode.BLEND_TRANSLUCENT)
    material.set_editor_property("two_sided", True)

    build = Builder(material)

    sampler = build.texture("BaseTexture")
    build.link(build.sub_uv(), "", sampler, "UVs")

    instance_rgb = build.custom_data_rgb(CD_COLOR)
    instance_alpha = build.custom_data(CD_ALPHA, 1.0)
    instance_emissive = build.custom_data(CD_EMISSIVE, 0.0)

    base_color = build.vector("BaseColor", unreal.LinearColor(1.0, 1.0, 1.0, 1.0))
    emissive_color = build.vector("EmissiveColor", unreal.LinearColor(1.0, 1.0, 1.0, 1.0))
    emissive_intensity = build.scalar("EmissiveIntensity", 0.0)
    opacity_param = build.scalar("Opacity", 1.0)

    colour = build.multiply(build.multiply(instance_rgb, "", base_color, "RGB"), "", sampler, "RGB")

    emissive_amount = build.add(instance_emissive, "", emissive_intensity, "")
    emission = build.multiply(build.multiply(colour, "", emissive_color, "RGB"), "", emissive_amount, "")

    source = build.add(colour, "", emission, "")
    alpha = build.multiply(build.multiply(sampler, "A", instance_alpha, ""), "", opacity_param, "")

    if additive:
        build.to_property(build.multiply(source, "", alpha, ""), "", unreal.MaterialProperty.MP_EMISSIVE_COLOR)
    else:
        build.to_property(source, "", unreal.MaterialProperty.MP_EMISSIVE_COLOR)
        build.to_property(alpha, "", unreal.MaterialProperty.MP_OPACITY)

    allow_instanced_static_meshes(material)
    MEL.layout_material_expressions(material)
    MEL.recompile_material(material)
    return material


def build_mesh():
    """
    Lit mesh particle / mesh instance: base colour, emission and a Fresnel rim.

    Masked rather than opaque, so a mesh particle whose opacity_over_life fades
    out actually disappears (an opaque debris chunk would pop). The mask is
    texA * instanceAlpha * Opacity against the default 1/3 threshold.

    FresnelPower 0 means "no rim": the rim is gated by clamp(FresnelPower, 0, 1)
    and its exponent is max(FresnelPower, 1), so a zero power contributes
    nothing instead of pow(x, 0) == 1.
    """
    material = create_material("M_AetherFX_Mesh")
    if material is None:
        return None

    material.set_editor_property("material_domain", unreal.MaterialDomain.MD_SURFACE)
    material.set_editor_property("shading_model", unreal.MaterialShadingModel.MSM_DEFAULT_LIT)
    material.set_editor_property("blend_mode", unreal.BlendMode.BLEND_MASKED)
    material.set_editor_property("two_sided", False)
    material.set_editor_property("opacity_mask_clip_value", 0.333)

    build = Builder(material)

    sampler = build.texture("BaseTexture")
    build.link(build.sub_uv(), "", sampler, "UVs")

    instance_rgb = build.custom_data_rgb(CD_COLOR)
    instance_alpha = build.custom_data(CD_ALPHA, 1.0)
    instance_emissive = build.custom_data(CD_EMISSIVE, 0.0)

    base_color = build.vector("BaseColor", unreal.LinearColor(1.0, 1.0, 1.0, 1.0))
    emissive_color = build.vector("EmissiveColor", unreal.LinearColor(1.0, 1.0, 1.0, 1.0))
    emissive_intensity = build.scalar("EmissiveIntensity", 0.0)
    fresnel_power = build.scalar("FresnelPower", 0.0)
    opacity_param = build.scalar("Opacity", 1.0)

    colour = build.multiply(build.multiply(instance_rgb, "", base_color, "RGB"), "", sampler, "RGB")
    build.to_property(colour, "", unreal.MaterialProperty.MP_BASE_COLOR)

    build.to_property(
        build.multiply(build.multiply(sampler, "A", instance_alpha, ""), "", opacity_param, ""),
        "", unreal.MaterialProperty.MP_OPACITY_MASK)

    emissive_amount = build.add(instance_emissive, "", emissive_intensity, "")
    emission = build.multiply(build.multiply(colour, "", emissive_color, "RGB"), "", emissive_amount, "")

    exponent = build.node(unreal.MaterialExpressionMax)
    build.link(fresnel_power, "", exponent, "A")
    build.link(build.constant(1.0), "", exponent, "B")

    strength = build.node(unreal.MaterialExpressionClamp, min_default=0.0, max_default=1.0)
    build.link(fresnel_power, "", strength, "")

    fresnel = build.node(unreal.MaterialExpressionFresnel, base_reflect_fraction=0.04)
    build.link(exponent, "", fresnel, "ExponentIn")

    rim = build.multiply(build.multiply(fresnel, "", strength, ""), "", emissive_color, "RGB")
    build.to_property(build.add(emission, "", rim, ""), "", unreal.MaterialProperty.MP_EMISSIVE_COLOR)

    allow_instanced_static_meshes(material)
    MEL.layout_material_expressions(material)
    MEL.recompile_material(material)
    return material


def build_decal():
    """Deferred decal: texture * BaseColor, emissive tint, texA * Opacity."""
    material = create_material("M_AetherFX_Decal")
    if material is None:
        return None

    material.set_editor_property("material_domain", unreal.MaterialDomain.MD_DEFERRED_DECAL)
    material.set_editor_property("blend_mode", unreal.BlendMode.BLEND_TRANSLUCENT)

    build = Builder(material)

    sampler = build.texture("BaseTexture")
    base_color = build.vector("BaseColor", unreal.LinearColor(1.0, 1.0, 1.0, 1.0))
    emissive_color = build.vector("EmissiveColor", unreal.LinearColor(1.0, 1.0, 1.0, 1.0))
    emissive_intensity = build.scalar("EmissiveIntensity", 0.0)
    opacity_param = build.scalar("Opacity", 1.0)

    colour = build.multiply(sampler, "RGB", base_color, "RGB")
    build.to_property(colour, "", unreal.MaterialProperty.MP_BASE_COLOR)
    build.to_property(
        build.multiply(build.multiply(colour, "", emissive_color, "RGB"), "", emissive_intensity, ""),
        "", unreal.MaterialProperty.MP_EMISSIVE_COLOR)
    build.to_property(build.multiply(sampler, "A", opacity_param, ""), "", unreal.MaterialProperty.MP_OPACITY)

    MEL.layout_material_expressions(material)
    MEL.recompile_material(material)
    return material


def build_ribbon():
    """
    Trails and beams: procedural strips with per-vertex colour.

    Blend mode is AlphaComposite (premultiplied: One, OneMinusSrcAlpha), which
    covers both AetherFX blend modes from one material -- the `Additive` scalar
    parameter forces Opacity to 0, turning premultiplied into plain additive.
    """
    material = create_material("M_AetherFX_Ribbon")
    if material is None:
        return None

    material.set_editor_property("material_domain", unreal.MaterialDomain.MD_SURFACE)
    material.set_editor_property("shading_model", unreal.MaterialShadingModel.MSM_UNLIT)
    material.set_editor_property("blend_mode", unreal.BlendMode.BLEND_ALPHA_COMPOSITE)
    material.set_editor_property("two_sided", True)

    build = Builder(material)

    sampler = build.texture("BaseTexture")
    vertex_color = build.node(unreal.MaterialExpressionVertexColor)
    base_color = build.vector("BaseColor", unreal.LinearColor(1.0, 1.0, 1.0, 1.0))
    emissive_intensity = build.scalar("EmissiveIntensity", 0.0)
    opacity_param = build.scalar("Opacity", 1.0)
    additive = build.scalar("Additive", 1.0)

    colour = build.multiply(build.multiply(vertex_color, "", base_color, "RGB"), "", sampler, "RGB")
    gain = build.add(emissive_intensity, "", build.constant(1.0), "")
    vertex_alpha = build.mask(vertex_color, "", a=True)
    alpha = build.multiply(build.multiply(vertex_alpha, "", sampler, "A"), "", opacity_param, "")

    # Premultiplied: emissive already carries the alpha.
    build.to_property(build.multiply(build.multiply(colour, "", gain, ""), "", alpha, ""),
                      "", unreal.MaterialProperty.MP_EMISSIVE_COLOR)

    one_minus_additive = build.node(unreal.MaterialExpressionOneMinus)
    build.link(additive, "", one_minus_additive, "")
    build.to_property(build.multiply(alpha, "", one_minus_additive, ""), "", unreal.MaterialProperty.MP_OPACITY)

    MEL.layout_material_expressions(material)
    MEL.recompile_material(material)
    return material


# ---------------------------------------------------------------------------

def main():
    if not unreal.EditorAssetLibrary.does_directory_exist(MATERIAL_PATH):
        unreal.EditorAssetLibrary.make_directory(MATERIAL_PATH)

    built = [
        build_billboard("M_AetherFX_Billboard_Additive", additive=True),
        build_billboard("M_AetherFX_Billboard_Translucent", additive=False),
        build_mesh(),
        build_decal(),
        build_ribbon(),
    ]

    for material in built:
        if material is None:
            continue
        path = material.get_path_name().split(".")[0]
        if unreal.EditorAssetLibrary.save_asset(path, only_if_is_dirty=False):
            log("saved {}".format(path))
        else:
            fail("could not save {}".format(path))

    if _failures:
        unreal.log_error("[AetherFX materials] FAILED with {} problem(s):".format(len(_failures)))
        for message in _failures:
            unreal.log_error("  - {}".format(message))
    else:
        log("OK: {} materials generated in {}".format(len([m for m in built if m]), MATERIAL_PATH))


main()
