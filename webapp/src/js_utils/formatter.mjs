// Generic functions for formatting

/**
 * Format a single number into a human-readable string.
 * Uses exponential notation for very large/small numbers.
 *
 * @param {number} num - The number to format.
 * @param {number} [maxDigits=4] - Maximum number of digits in of the formatted string.
 * @returns {string} - Formatted number as a string.
 */
export function formatNumber(num, maxDigits = 4) {
  if (typeof num !== "number" || isNaN(num)) return String(num);

  // First convert to just a number
  const normal = String(num);
  // Use toPrecision to handle rounding and turnign to exponential
  const rounded = num.toPrecision(maxDigits);

  // Return whichever string is shorter, preventing trailing zeros on decimals
  return rounded.length < normal.length ? rounded : normal;
}

/**
 * Recursively format numbers inside arrays or objects.
 *
 * @param {any} value - Value to format (number, array, object, or other).
 * @param {number} [maxDigits=4] - Maximum number of digits in each number of the formatted string.
 * @returns {string} - A formatted display string.
 */
export function formatValue(value, maxDigits = 4) {
  if (Array.isArray(value)) {
    const items = value.map((val) => formatValue(val, maxDigits));
    return `[${items.join(", ")}]`;
  }
  if (typeof value === "object" && value !== null) {
    const entries = Object.entries(value).map(([key, val]) => {
      return `${key}: ${formatValue(val, maxDigits)}`;
    });
    return `{${entries.join(", ")}}`;
  }

  // Attempt to coerce to number as numbers in input boxes may be represented as strings
  let asNum = Number(value);
  if (!Number.isNaN(asNum)) {
    return formatNumber(asNum, maxDigits);
  }
  // Only if the string will not coerce to a number do we output the value.
  return value;
}

/**
 * Format a date from a unix timestamp in s
 *
 * @param {number} - Unix timestamp in s
 * @returns {string} - A formatted date and time string.
 */
export function formatDate(timestamp) {
  // Multiply by 1000 as JS uses ms not s
  let d = new Date(timestamp * 1000);
  // Convert to a string in a very javascript way!
  let yyyy = d.getFullYear();
  let mm = d.getMonth() + 1;
  let dd = d.getDate();
  let HH = d.getHours().toString().padStart(2, "0");
  let MM = d.getMinutes().toString().padStart(2, "0");
  return `${HH}:${MM} ${dd}/${mm}/${yyyy}`;
}

/**
 * Format a duration given in seconds as "MM:SS", or "HH:MM:SS" once it
 * reaches an hour or more.
 *
 * Returns null if given null/undefined, so callers can decide how to
 * represent "no duration yet" rather than this function guessing.
 * @param {number} - Duration in seconds
 * @returns {string} - A formatted duration with hours, mins, and seconds
 */
export function formatDuration(duration) {
  if (duration == null || isNaN(duration)) return null;

  const h = Math.floor(duration / 3600);
  const m = Math.floor((duration % 3600) / 60);
  const s = Math.floor(duration % 60);

  const m_pad = String(m).padStart(2, "0");
  const s_pad = String(s).padStart(2, "0");

  if (h > 0) return `${h}h ${m_pad}m ${s_pad}s`;
  if (m > 0) return `${m_pad}m ${s_pad}s`;
  return `${s_pad}s`;
}

/**
 * Format a raw value for display.
 *
 * - Arrays are joined with " x ", e.g. [1024, 768] -> "1024 x 768"
 * - Booleans become "Yes" / "No"
 * - null/undefined become an em dash "—"
 * - Anything else is returned unchanged
 */
export function formatInfoValue(val) {
  if (Array.isArray(val)) return val.join(" x ");
  if (typeof val === "boolean") return val ? "Yes" : "No";
  if (val === null || val === undefined) return "—";
  return val;
}

/**
 * Format a snake_case key into Title Case with spaces.
 *
 * e.g. "autofocus_dz" -> "Autofocus Dz"
 */
export function formatKey(key) {
  return key
    .split("_")
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
    .join(" ");
}

/**
 * Flatten a (possibly nested) settings object into a single flat
 * { label: value } map, formatting both keys and values along the way.
 *
 * Nested objects are flattened with their parent key's context preserved in
 * the label (joined by ": "), rather than being merged in together and
 * losing that context. For example:
 *
 *   flattenGroup({
 *     key1: { inner_key: "foo", other_key: "bar" },
 *     key2: "foobar",
 *   })
 *
 * returns:
 *
 *   {
 *     "Key1: Inner Key": "foo",
 *     "Key1: Other Key": "bar",
 *     "Key2": "foobar",
 *   }
 *
 * This nests to arbitrary depth, e.g. a value three levels deep would be
 * labelled "Key1: Key2: Key3".
 *
 * @param {Object} obj - The object to flatten.
 * @param {String} prefix - The (already-formatted) label context to prepend to each
 *     key at this level. Leave unset when calling from the top level.
 */
export function flattenGroup(obj, prefix = "") {
  const items = {};
  Object.entries(obj).forEach(([key, value]) => {
    const label = prefix ? `${prefix}: ${formatKey(key)}` : formatKey(key);
    if (value !== null && typeof value === "object" && !Array.isArray(value)) {
      Object.assign(items, flattenGroup(value, label));
    } else {
      items[label] = formatValue(value);
    }
  });
  return items;
}
