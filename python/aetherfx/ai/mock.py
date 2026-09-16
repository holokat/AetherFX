"""A deterministic, offline :class:`ReferenceAnalyzer` for tests and demos.

:class:`MockAnalyzer` ignores the pixels it is given and always returns the same
"giant circular fire AOE" analysis.  Its layers and implementation plan mirror
``examples/effects/fire_aoe.json`` - the same six layers (telegraph, ignition,
primary, secondary, interaction, aftermath) and the same node mix - so the
planner, the MCP server and the evaluation loop can be exercised end to end
without a network, an API key or a GPU.

Because it is deterministic it is also the fixture the planner tests assert
against: change the canned document and those expectations change with it.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import ClassVar

from ..ead import (
    ChangeSuggestion,
    DominantColor,
    EffectAnalysisDocument,
    GlobalAnalysis,
    ImplementationPlanEntry,
    LayerAnalysis,
    MotionHypothesis,
    OcclusionPair,
    PlannedNode,
    PrimaryForm,
    RenderEvaluation,
    Source,
    TemporalHypothesis,
    TemporalPhase,
    WorldHypothesis,
)
from .base import ImageInput, ReferenceAnalyzer, Segment

__all__ = ["MockAnalyzer", "canned_fire_aoe_ead", "canned_render_evaluation"]


def _source_path(image: ImageInput | None) -> str | None:
    """Best-effort path for the ``source.path`` member; ``None`` for raw bytes."""
    if image is None or isinstance(image, (bytes, bytearray)):
        return None
    return str(Path(os.fspath(image)))


def canned_fire_aoe_ead(
    image: ImageInput | None = None, prompt: str | None = None
) -> EffectAnalysisDocument:
    """Build the canned fire-AOE EAD.

    The six layers and their implementation plan mirror
    ``examples/effects/fire_aoe.json``:

    * ``telegraph`` - a ground rune decal plus embers gathering on a ring.
    * ``ignition`` - a one-frame flash light and an outward burst.
    * ``primary`` - the outer flame wall and the central eruption.
    * ``secondary`` - sparks and smoke.
    * ``interaction`` - the fire light and heat haze it casts on the scene.
    * ``aftermath`` - drifting embers and a scorch decal.
    """
    return EffectAnalysisDocument(
        ead_version="0.1.0",
        source=Source(kind="image", path=_source_path(image), prompt=prompt),
        **{
            "global": GlobalAnalysis(
                effect_category="fire_aoe",
                estimated_scale_m=6.0,
                visual_style="stylized",
                dominant_colors=[
                    DominantColor(rgb=(1.0, 0.42, 0.06), share=0.42, role="flame"),
                    DominantColor(rgb=(1.0, 0.9, 0.55), share=0.18, role="hot core"),
                    DominantColor(rgb=(0.18, 0.16, 0.15), share=0.24, role="smoke"),
                    DominantColor(rgb=(0.02, 0.015, 0.01), share=0.1, role="scorch"),
                    DominantColor(rgb=(1.0, 0.7, 0.25), share=0.06, role="sparks"),
                ],
                contrast="high",
                energy="violent",
                symmetry="radial",
                camera_angle="three_quarter",
                probable_duration_s=4.5,
                confidence=0.7,
            )
        },
        primary_forms=[
            PrimaryForm(form="ring", screen_bbox=(0.12, 0.42, 0.88, 0.86), role="outer flame wall", confidence=0.82),
            PrimaryForm(form="column", screen_bbox=(0.38, 0.18, 0.62, 0.78), role="central eruption", confidence=0.66),
            PrimaryForm(form="circle", screen_bbox=(0.1, 0.6, 0.9, 0.95), role="ground rune / scorch", confidence=0.6),
            PrimaryForm(form="cloud", screen_bbox=(0.05, 0.05, 0.95, 0.6), role="smoke canopy", confidence=0.55),
        ],
        layers=[
            LayerAnalysis(
                id="telegraph",
                semantic_role="telegraph",
                primitive="decal",
                shape_hint="ring",
                screen_coverage=0.18,
                world_hypothesis=WorldHypothesis(center=(0.0, 0.0, 0.0), radius_m=3.2, height_m=0.05),
                depth="background",
                color=(1.0, 0.35, 0.05),
                brightness="emissive",
                opacity="additive_glow",
                material_behavior="energy",
                motion_hypothesis=MotionHypothesis(
                    inferred=True,
                    description="rune fades in while embers gather inward along the ring",
                    speed_mps=0.2,
                    direction=(0.0, 1.0, 0.0),
                ),
                simulation_type="analytic",
                confidence=0.6,
            ),
            LayerAnalysis(
                id="ignition",
                semantic_role="ignition",
                primitive="emitter",
                shape_hint="disc",
                screen_coverage=0.2,
                world_hypothesis=WorldHypothesis(center=(0.0, 0.5, 0.0), radius_m=0.5, height_m=1.0),
                depth="midground",
                inside_of="primary",
                color=(1.0, 0.95, 0.7),
                brightness="emissive_high",
                opacity="additive_glow",
                material_behavior="flame",
                motion_hypothesis=MotionHypothesis(
                    inferred=True,
                    description="single outward blast from the centre with a hard flash",
                    speed_mps=12.0,
                    direction=(0.0, 0.2, 0.0),
                ),
                simulation_type="particles",
                confidence=0.62,
            ),
            LayerAnalysis(
                id="primary",
                semantic_role="primary",
                primitive="emitter",
                shape_hint="ring",
                screen_coverage=0.34,
                world_hypothesis=WorldHypothesis(center=(0.0, 0.0, 0.0), radius_m=3.0, height_m=1.5),
                depth="midground",
                color=(1.0, 0.45, 0.08),
                brightness="emissive_high",
                opacity="additive_glow",
                material_behavior="flame",
                motion_hypothesis=MotionHypothesis(
                    inferred=True,
                    description="rising turbulent flames on an annulus, plus a central eruption",
                    speed_mps=2.5,
                    direction=(0.0, 1.0, 0.0),
                ),
                simulation_type="particles",
                confidence=0.78,
            ),
            LayerAnalysis(
                id="secondary",
                semantic_role="secondary",
                primitive="particle_system",
                shape_hint="ring",
                screen_coverage=0.22,
                world_hypothesis=WorldHypothesis(center=(0.0, 0.4, 0.0), radius_m=2.9, height_m=2.5),
                depth="foreground",
                inside_of="primary",
                color=(1.0, 0.7, 0.25),
                brightness="emissive",
                opacity="additive_glow",
                material_behavior="sparks",
                motion_hypothesis=MotionHypothesis(
                    inferred=True,
                    description="sparks thrown outward and upward, smoke lifting above the wall",
                    speed_mps=5.0,
                    direction=(0.0, 1.0, 0.0),
                ),
                simulation_type="particles",
                confidence=0.6,
            ),
            LayerAnalysis(
                id="interaction",
                semantic_role="interaction",
                primitive="light",
                shape_hint="sphere",
                screen_coverage=0.4,
                world_hypothesis=WorldHypothesis(center=(0.0, 1.2, 0.0), radius_m=14.0),
                depth="background",
                color=(1.0, 0.5, 0.15),
                brightness="emissive",
                opacity="additive_glow",
                material_behavior="energy",
                motion_hypothesis=MotionHypothesis(
                    inferred=True, description="flickering orange bounce light and heat distortion", speed_mps=0.0
                ),
                simulation_type="analytic",
                confidence=0.5,
            ),
            LayerAnalysis(
                id="aftermath",
                semantic_role="aftermath",
                primitive="particle_system",
                shape_hint="disc",
                screen_coverage=0.14,
                world_hypothesis=WorldHypothesis(center=(0.0, 0.2, 0.0), radius_m=3.0, height_m=2.0),
                depth="midground",
                color=(1.0, 0.45, 0.1),
                brightness="emissive",
                opacity="additive_glow",
                material_behavior="sparks",
                motion_hypothesis=MotionHypothesis(
                    inferred=True, description="embers drift upward over a darkened scorch mark", speed_mps=0.8,
                    direction=(0.0, 1.0, 0.0),
                ),
                simulation_type="particles",
                confidence=0.55,
            ),
        ],
        temporal_hypothesis=TemporalHypothesis(
            inferred=True,
            phases=[
                TemporalPhase(name="anticipation", start=0.0, end=0.8, description="rune fades in, embers gather"),
                TemporalPhase(name="activation", start=0.8, end=1.0, description="flash and outward burst"),
                TemporalPhase(name="peak", start=1.0, end=1.6, description="flame wall and eruption at full rate"),
                TemporalPhase(name="sustain", start=1.6, end=3.0, description="wall burns, smoke builds"),
                TemporalPhase(name="decay", start=3.0, end=4.5, description="flames die down, embers drift"),
            ],
        ),
        occlusion=[
            OcclusionPair(front="secondary", behind="primary"),
            OcclusionPair(front="primary", behind="telegraph"),
            OcclusionPair(front="aftermath", behind="telegraph"),
        ],
        implementation_plan=[
            ImplementationPlanEntry(
                layer="telegraph",
                nodes=[
                    PlannedNode.model_validate(
                        {"type": "decal", "shape": "circle", "size": [6.0, 6.0], "emissive": 3.0, "blend": "additive"}
                    ),
                    PlannedNode.model_validate({"type": "texture", "width": 256, "height": 256}),
                    PlannedNode.model_validate(
                        {"type": "particle_system", "max_particles": 800, "lifetime": 0.9, "size": 0.05,
                         "emissive": 4.0, "blend": "additive"}
                    ),
                    PlannedNode.model_validate(
                        {"type": "emitter", "shape": "ring", "radius": 3.2, "inner_radius": 2.6, "rate": 300,
                         "velocity": 0.2, "phase": "anticipation"}
                    ),
                    PlannedNode.model_validate(
                        {"type": "force", "force_type": "attractor", "strength": 6.0, "radius": 4.0,
                         "falloff": "smooth"}
                    ),
                ],
            ),
            ImplementationPlanEntry(
                layer="ignition",
                nodes=[
                    PlannedNode.model_validate(
                        {"type": "light", "light_type": "point", "intensity": 120.0, "radius": 12.0,
                         "phase": "activation"}
                    ),
                    PlannedNode.model_validate(
                        {"type": "particle_system", "max_particles": 1500, "lifetime": 0.5, "size": 0.35,
                         "emissive": 4.0, "drag": 3.0, "blend": "additive"}
                    ),
                    PlannedNode.model_validate(
                        {"type": "emitter", "shape": "disc", "radius": 0.5, "rate": 0, "burst_count": 900,
                         "velocity": 12.0, "phase": "activation"}
                    ),
                    PlannedNode.model_validate(
                        {"type": "material", "blend": "additive", "emissive_intensity": 3.0, "soft_particle": True}
                    ),
                    PlannedNode.model_validate(
                        {"type": "force", "force_type": "curl_noise", "strength": 2.5, "frequency": 0.8}
                    ),
                ],
            ),
            ImplementationPlanEntry(
                layer="primary",
                nodes=[
                    PlannedNode.model_validate(
                        {"type": "particle_system", "max_particles": 6000, "lifetime": 0.7, "size": 0.45,
                         "emissive": 3.0, "drag": 1.5, "blend": "additive"}
                    ),
                    PlannedNode.model_validate(
                        {"type": "emitter", "shape": "ring", "radius": 3.0, "inner_radius": 2.5, "rate": 2200,
                         "velocity": 2.5, "spread": 15, "start_time": 0.8}
                    ),
                    PlannedNode.model_validate(
                        {"type": "emitter", "shape": "disc", "radius": 0.6, "rate": 1500, "velocity": 6.0,
                         "spread": 12, "start_time": 0.9}
                    ),
                    PlannedNode.model_validate(
                        {"type": "force", "force_type": "curl_noise", "strength": 2.5, "frequency": 0.8, "octaves": 2}
                    ),
                    PlannedNode.model_validate({"type": "force", "force_type": "buoyancy", "strength": 2.0}),
                    PlannedNode.model_validate(
                        {"type": "material", "blend": "additive", "emissive_intensity": 1.0, "depth_fade": 0.15}
                    ),
                    PlannedNode.model_validate({"type": "texture", "width": 128, "height": 128}),
                ],
            ),
            ImplementationPlanEntry(
                layer="secondary",
                nodes=[
                    PlannedNode.model_validate(
                        {"type": "particle_system", "max_particles": 3000, "lifetime": 1.0, "size": 0.03,
                         "emissive": 6.0, "render_mode": "stretched_billboard", "velocity_stretch": 0.12,
                         "blend": "additive"}
                    ),
                    PlannedNode.model_validate(
                        {"type": "emitter", "shape": "ring", "radius": 2.9, "inner_radius": 2.4, "rate": 900,
                         "velocity": 5.0, "spread": 40, "start_time": 0.85}
                    ),
                    PlannedNode.model_validate(
                        {"type": "force", "force_type": "turbulence", "strength": 3.0, "frequency": 1.2}
                    ),
                    PlannedNode.model_validate({"type": "texture", "width": 32, "height": 32}),
                ],
            ),
            ImplementationPlanEntry(
                layer="interaction",
                nodes=[
                    PlannedNode.model_validate(
                        {"type": "light", "light_type": "point", "intensity": 40.0, "radius": 14.0,
                         "flicker_amplitude": 0.2, "flicker_frequency": 11.0, "start_time": 0.9}
                    ),
                    PlannedNode.model_validate(
                        {"type": "post_effect", "post_type": "heat_haze", "intensity": 0.5, "frequency": 5.0,
                         "start_time": 0.9, "duration": 3.0}
                    ),
                ],
            ),
            ImplementationPlanEntry(
                layer="aftermath",
                nodes=[
                    PlannedNode.model_validate(
                        {"type": "particle_system", "max_particles": 1200, "lifetime": 2.0, "size": 0.025,
                         "emissive": 4.0, "drag": 1.0, "blend": "additive"}
                    ),
                    PlannedNode.model_validate(
                        {"type": "emitter", "shape": "disc", "radius": 3.0, "rate": 250, "velocity": 0.8,
                         "phase": "decay"}
                    ),
                    PlannedNode.model_validate(
                        {"type": "force", "force_type": "buoyancy", "strength": 1.5}
                    ),
                    PlannedNode.model_validate(
                        {"type": "decal", "shape": "circle", "size": [6.6, 6.6], "blend": "alpha",
                         "start_time": 0.9}
                    ),
                    PlannedNode.model_validate({"type": "texture", "width": 256, "height": 256}),
                ],
            ),
        ],
        open_questions=[
            "is the ground rune animated, or a static texture faded in?",
            "does the camera move during the effect?",
            "how long does the scorch decal persist after the flames stop?",
        ],
    )


def canned_render_evaluation() -> RenderEvaluation:
    """The canned verdict :meth:`MockAnalyzer.evaluate_render` always returns."""
    return RenderEvaluation(
        score=0.62,
        summary=(
            "The silhouette and the ring placement read correctly, but the render is dimmer and "
            "cooler than the reference and the flame wall is too short."
        ),
        matches=[
            "radial ring silhouette at roughly the right screen scale",
            "warm orange base palette",
            "ground scorch present after the flames die down",
        ],
        mismatches=[
            "hot core is missing: the reference has near-white highlights the render never reaches",
            "flame wall reads about half as tall as the reference",
            "smoke is too thin, so the upper half of the frame stays empty",
        ],
        suggestions=[
            ChangeSuggestion(
                layer="primary",
                node="primary_particle_system",
                parameter="emissive",
                current=3.0,
                proposed=5.5,
                reason="the reference clips to white in the flame core; raise emissive to reach HDR highlights",
            ),
            ChangeSuggestion(
                layer="primary",
                node="primary_emitter",
                parameter="velocity",
                current=2.5,
                proposed=4.0,
                reason="flames rise about twice as high in the reference over the same lifetime",
            ),
            ChangeSuggestion(
                layer="secondary",
                node="secondary_particle_system",
                parameter="max_particles",
                current=3000,
                proposed=4500,
                reason="spark density in the reference is noticeably higher along the ring",
            ),
        ],
        confidence=0.55,
    )


class MockAnalyzer(ReferenceAnalyzer):
    """Deterministic analyzer that always returns the canned fire-AOE analysis.

    Nothing is read from the image beyond its path, which is recorded in
    ``source.path``.  Use it in tests, in offline demos and as the default so
    the package works with no API key configured.
    """

    name: ClassVar[str] = "mock"

    def analyze_reference(self, image: ImageInput, prompt: str | None = None) -> EffectAnalysisDocument:
        """Return :func:`canned_fire_aoe_ead` with ``source`` filled from the input."""
        return canned_fire_aoe_ead(image, prompt)

    def segment_reference(self, image: ImageInput) -> list[Segment]:
        """Return boxes for the canned primary forms; no pixel masks are produced."""
        document = canned_fire_aoe_ead(image)
        segments: list[Segment] = []
        for form in document.primary_forms:
            segments.append(
                Segment(
                    label=form.role,
                    bbox=form.screen_bbox or (0.0, 0.0, 1.0, 1.0),
                    mask=None,
                    confidence=form.confidence,
                )
            )
        return segments

    def describe_effect(self, image: ImageInput) -> str:
        """Return a fixed plain-language description of the canned effect."""
        return (
            "A large radial fire area-of-effect seen from a three-quarter angle: a glowing rune "
            "telegraphs on the ground, a flash ignites it, a ring of turbulent flame erupts around "
            "a central column, sparks and smoke rise from the ring, warm flickering light spills "
            "onto the ground, and embers drift over a scorch mark as it dies down."
        )

    def evaluate_render(
        self,
        reference: ImageInput,
        render: ImageInput,
        ead: EffectAnalysisDocument | None = None,
    ) -> RenderEvaluation:
        """Return :func:`canned_render_evaluation`, ignoring both images."""
        return canned_render_evaluation()
