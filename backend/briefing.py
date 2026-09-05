"""Responder Safety Briefing — Deterministic, Rule-Based Hazard Assessment.

When a crew is dispatched to a call, generate a concise briefing on the specific
hazards they are heading into and required operational precautions.

Discipline:
The set of warnings is strictly decided by rule against structured,
already-computed fields -- NEVER invented or hallucinated by a model.
No rule, no warning; nothing is inferred.
"""
from typing import Any, Optional


def generate_briefing(
    call_fields: Optional[dict] = None,
    exposure: Optional[dict] = None,
    route: Optional[dict] = None,
) -> dict:
    """Generate responder safety warnings strictly derived from computed telemetry.

    Parameters:
        call_fields: dict containing hazard_class, trapped, medical_critical, access_constraint
        exposure: dict containing hand_m, p75, etc.
        route: dict from route_to() containing max_exposure_on_route, blocked_on_direct, detour_m

    Returns:
        {
            "warnings": [str, ...],
            "route_hazards": [{"name": ..., "exposure": ..., "hand_m": ...}, ...],
            "generated_from": [str, ...],  # audit trail of trigger keys
        }
    """
    call_fields = call_fields or {}
    exposure = exposure or {}
    route = route or {}

    warnings: list[str] = []
    generated_from: list[str] = []
    route_hazards: list[dict[str, Any]] = []

    # 1. Site ground-level flooding check
    hazard_class = str(call_fields.get("hazard_class") or "").strip().lower()
    hand_m = exposure.get("hand_m")
    if hazard_class == "flood" and hand_m is not None and float(hand_m) < 3.0:
        warnings.append(
            "Ground-level flooding at the site. Do not wade — use boat approach."
        )
        generated_from.append("hand_m")

    # 2. Blocked / flooded road segments on approach route
    blocked_on_direct = route.get("blocked_on_direct") or []
    if blocked_on_direct:
        generated_from.append("blocked_on_direct")
        for seg in blocked_on_direct:
            name = seg.get("name") or "Unmarked road"
            seg_hand = seg.get("hand_m")
            route_hazards.append({
                "name": name,
                "exposure": seg.get("exposure"),
                "hand_m": seg_hand,
            })
            if seg_hand is not None:
                warnings.append(
                    f"{name}: {seg_hand} m above drainage, do not attempt to cross."
                )
            else:
                warnings.append(
                    f"{name}: submerged, do not attempt to cross."
                )

    # 3. Access constraint
    access_constraint = str(call_fields.get("access_constraint") or "").strip().lower()
    if access_constraint == "water_on_road":
        warnings.append(
            "Vehicle access likely blocked short of the site; final approach on foot or boat."
        )
        generated_from.append("access_constraint")

    # 4. Landslide hazard
    if hazard_class == "landslide":
        warnings.append(
            "Unstable slope reported. Do not idle beneath the scarp; watch for further movement."
        )
        generated_from.append("hazard_class")

    # 5. Medical critical
    if bool(call_fields.get("medical_critical")):
        warnings.append(
            "Medical-critical caller — bring trauma/medical kit, confirm airway status on arrival."
        )
        generated_from.append("medical_critical")

    # 6. Elevated route exposure
    max_exposure = route.get("max_exposure_on_route")
    if max_exposure is not None and float(max_exposure) > 0.5:
        warnings.append(
            "Approach route itself crosses elevated-exposure ground — proceed slowly, verify each low point."
        )
        generated_from.append("max_exposure_on_route")

    return {
        "warnings": warnings,
        "route_hazards": route_hazards,
        "generated_from": generated_from,
    }
