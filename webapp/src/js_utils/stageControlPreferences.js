/** Resolve one manual step in physical units, without bypassing the hardware profile. */
export function manualStageStep(axis, savedMm) {
  const mm = savedMm === undefined ? axis?.ui_step_mm : savedMm;
  const unitsPerMm = axis?.units_per_mm;
  const units = Math.round(mm * unitsPerMm);
  if (
    typeof mm !== "number" ||
    !Number.isFinite(mm) ||
    mm <= 0 ||
    !Number.isFinite(unitsPerMm) ||
    unitsPerMm <= 0 ||
    !Number.isFinite(axis?.max_move_mm) ||
    (axis.single_move_limit_enabled !== false && mm > axis.max_move_mm) ||
    !Number.isSafeInteger(units) ||
    units < 1 ||
    Math.abs(mm * unitsPerMm - units) > 1e-6
  ) {
    return { mm, units: 0, valid: false };
  }
  return { mm, units, valid: true };
}
