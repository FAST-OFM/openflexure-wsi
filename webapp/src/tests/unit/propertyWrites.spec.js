import axios from "axios";
import { afterEach, describe, expect, it, vi } from "vitest";
import labThingsMixins from "../../mixins/labThingsMixins.js";

describe("JSON property writes", () => {
  const originalAdapter = axios.defaults.adapter;
  afterEach(() => {
    axios.defaults.adapter = originalAdapter;
  });

  it.each([
    ["focus strategy", "smart_stack"],
    ["single autofocus", "single_autofocus"],
    ["boolean-looking string", "false"],
    ["number-looking string", "50"],
    ["object-looking string", '{"x":1}'],
    ["empty string", ""],
    ["false", false],
    ["true", true],
    ["zero", 0],
    ["number", 5.5],
    ["null", null],
    ["array", [1, 2, 3]],
    ["object", { x: 1, enabled: false }],
  ])("preserves the type and value of %s at the HTTP boundary", async (_, value) => {
    // Use real Axios serialization; only the final network adapter is replaced.
    const adapter = vi.fn(async (config) => {
      expect(config.headers.get("Content-Type")).toBe("application/json");
      expect(JSON.parse(config.data)).toEqual(value);
      return { status: 201, statusText: "Created", data: null, headers: {}, config };
    });
    axios.defaults.adapter = adapter;
    const url = "/api/v3/fast_ofm_scan_workflow/focus_strategy";
    const vm = {
      wotStore: { thingPropertyUrl: vi.fn(() => url) },
    };
    await labThingsMixins.methods.writeThingProperty.call(
      vm,
      "fast_ofm_scan_workflow",
      "focus_strategy",
      value,
    );
    expect(adapter).toHaveBeenCalledOnce();
    expect(adapter.mock.calls[0][0].method).toBe("put");
    expect(adapter.mock.calls[0][0].url).toBe(url);
  });
});
