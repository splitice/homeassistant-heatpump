from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterable

from .constants import (
    HVAC_FAN_ONLY,
    HVAC_HEAT,
    IDLE_MIXING_COOLDOWN_SECONDS,
    IDLE_MIXING_DOWNSTAIRS_ZONE_KEY,
    IDLE_MIXING_ENTRY_DELAY_SECONDS,
    IDLE_MIXING_ENTRY_OFFICE_EXCESS,
    IDLE_MIXING_ENTRY_TEMPERATURE_SPREAD,
    IDLE_MIXING_EXIT_OFFICE_EXCESS,
    IDLE_MIXING_EXIT_TEMPERATURE_SPREAD,
    IDLE_MIXING_MAX_SECONDS,
    IDLE_MIXING_OFFICE_ZONE_KEY,
)
from .models import DemandSnapshot, EquipmentDemand


@dataclass(frozen=True)
class IdleMixingDecision:
    active: bool
    zone_keys: tuple[str, ...]
    started_at: datetime | None
    cooldown_until: datetime | None
    office_temperature: float | None
    downstairs_temperature: float | None
    office_excess: float | None
    temperature_spread: float | None
    reason: str


def _normalize_timestamp(value: datetime | None) -> datetime | None:
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _has_thermal_demand(demand: EquipmentDemand) -> bool:
    return bool(
        demand.heat_requested
        or demand.maintain_heat_mode
        or demand.cool_requested
        or demand.maintain_cool_mode
        or demand.dry_requested
    )


def _decision(
    *,
    active: bool,
    started_at: datetime | None,
    cooldown_until: datetime | None,
    reason: str,
    office_temperature: float | None = None,
    downstairs_temperature: float | None = None,
    office_excess: float | None = None,
    temperature_spread: float | None = None,
) -> IdleMixingDecision:
    return IdleMixingDecision(
        active=active,
        zone_keys=(IDLE_MIXING_DOWNSTAIRS_ZONE_KEY, IDLE_MIXING_OFFICE_ZONE_KEY) if active else (),
        started_at=started_at if active else None,
        cooldown_until=cooldown_until,
        office_temperature=office_temperature,
        downstairs_temperature=downstairs_temperature,
        office_excess=office_excess,
        temperature_spread=temperature_spread,
        reason=reason,
    )


def resolve_idle_mixing(
    snapshot: DemandSnapshot,
    demand: EquipmentDemand,
    *,
    operation_mode: str | None,
    supported_hvac_modes: Iterable[object] | None,
    idle_started_at: datetime | None,
    active: bool,
    started_at: datetime | None,
    cooldown_until: datetime | None,
    now: datetime,
) -> IdleMixingDecision:
    """Resolve a low-speed fan-only cycle that redistributes excess Office heat."""
    normalized_now = _normalize_timestamp(now) or now
    normalized_started_at = _normalize_timestamp(started_at)
    normalized_cooldown_until = _normalize_timestamp(cooldown_until)

    if snapshot.poweroff_forced_off:
        return _decision(
            active=False,
            started_at=None,
            cooldown_until=normalized_cooldown_until,
            reason="PowerOff takes priority over idle mixing",
        )
    if _has_thermal_demand(demand):
        return _decision(
            active=False,
            started_at=None,
            cooldown_until=normalized_cooldown_until,
            reason="thermal demand takes priority over idle mixing",
        )
    if operation_mode != HVAC_HEAT:
        return _decision(
            active=False,
            started_at=None,
            cooldown_until=normalized_cooldown_until,
            reason="idle mixing requires heating operation",
        )

    normalized_supported_modes = {
        str(mode).strip().lower() for mode in supported_hvac_modes or ()
    }
    if HVAC_FAN_ONLY not in normalized_supported_modes:
        return _decision(
            active=False,
            started_at=None,
            cooldown_until=normalized_cooldown_until,
            reason="climate entity does not advertise fan_only mode",
        )

    office = snapshot.zones.get(IDLE_MIXING_OFFICE_ZONE_KEY)
    downstairs = snapshot.zones.get(IDLE_MIXING_DOWNSTAIRS_ZONE_KEY)
    if office is None or downstairs is None or not office.is_enabled_by_mode or not downstairs.is_enabled_by_mode:
        return _decision(
            active=False,
            started_at=None,
            cooldown_until=normalized_cooldown_until,
            reason="Office and Downstairs must both be enabled for idle mixing",
        )

    office_excess = office.current_temp - office.scheme.ideal_target
    temperature_spread = office.current_temp - downstairs.current_temp
    measurements = {
        "office_temperature": office.current_temp,
        "downstairs_temperature": downstairs.current_temp,
        "office_excess": office_excess,
        "temperature_spread": temperature_spread,
    }

    if active:
        elapsed_seconds = (
            (normalized_now - normalized_started_at).total_seconds()
            if normalized_started_at is not None and normalized_now >= normalized_started_at
            else 0.0
        )
        if elapsed_seconds >= IDLE_MIXING_MAX_SECONDS:
            cooldown = normalized_now + timedelta(seconds=IDLE_MIXING_COOLDOWN_SECONDS)
            return _decision(
                active=False,
                started_at=None,
                cooldown_until=cooldown,
                reason=f"idle mixing reached its {IDLE_MIXING_MAX_SECONDS // 60}-minute maximum",
                **measurements,
            )
        if (
            office_excess < IDLE_MIXING_EXIT_OFFICE_EXCESS
            or temperature_spread < IDLE_MIXING_EXIT_TEMPERATURE_SPREAD
        ):
            cooldown = normalized_now + timedelta(seconds=IDLE_MIXING_COOLDOWN_SECONDS)
            return _decision(
                active=False,
                started_at=None,
                cooldown_until=cooldown,
                reason=(
                    f"idle mixing satisfied: Office excess {office_excess:.1f}C, "
                    f"Office/Downstairs spread {temperature_spread:.1f}C"
                ),
                **measurements,
            )
        return _decision(
            active=True,
            started_at=normalized_started_at or normalized_now,
            cooldown_until=None,
            reason=(
                f"continuing idle mixing: Office excess {office_excess:.1f}C, "
                f"Office/Downstairs spread {temperature_spread:.1f}C"
            ),
            **measurements,
        )

    if normalized_cooldown_until is not None and normalized_now < normalized_cooldown_until:
        return _decision(
            active=False,
            started_at=None,
            cooldown_until=normalized_cooldown_until,
            reason=f"idle mixing cooldown until {normalized_cooldown_until.isoformat()}",
            **measurements,
        )

    normalized_idle_started_at = _normalize_timestamp(idle_started_at)
    idle_seconds = (
        (normalized_now - normalized_idle_started_at).total_seconds()
        if normalized_idle_started_at is not None and normalized_now >= normalized_idle_started_at
        else 0.0
    )
    if idle_seconds < IDLE_MIXING_ENTRY_DELAY_SECONDS:
        return _decision(
            active=False,
            started_at=None,
            cooldown_until=None,
            reason=(
                f"idle mixing entry delay: {idle_seconds:.0f}/{IDLE_MIXING_ENTRY_DELAY_SECONDS}s"
            ),
            **measurements,
        )
    if (
        office_excess < IDLE_MIXING_ENTRY_OFFICE_EXCESS
        or temperature_spread < IDLE_MIXING_ENTRY_TEMPERATURE_SPREAD
    ):
        return _decision(
            active=False,
            started_at=None,
            cooldown_until=None,
            reason=(
                f"idle mixing not needed: Office excess {office_excess:.1f}C "
                f"(need {IDLE_MIXING_ENTRY_OFFICE_EXCESS:.1f}C), spread {temperature_spread:.1f}C "
                f"(need {IDLE_MIXING_ENTRY_TEMPERATURE_SPREAD:.1f}C)"
            ),
            **measurements,
        )

    return _decision(
        active=True,
        started_at=normalized_now,
        cooldown_until=None,
        reason=(
            f"starting idle mixing: Office {office.current_temp:.1f}C is "
            f"{office_excess:.1f}C above ideal and {temperature_spread:.1f}C above Downstairs"
        ),
        **measurements,
    )
