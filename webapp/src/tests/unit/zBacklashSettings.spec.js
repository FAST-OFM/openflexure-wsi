import { shallowMount, flushPromises } from "@vue/test-utils";
import { createTestingPinia } from "@pinia/testing";
import { describe, it, expect, vi, afterEach } from "vitest";
import ZBacklashSettings from "../../components/tabContentComponents/settingsComponents/zBacklashSettings.vue";
import ActionButton from "../../components/labThingsComponents/actionButton.vue";

vi.mock("@vueuse/core", () => ({
  useIntersectionObserver: () => ({ stop: vi.fn() }),
}));

let wrapper;
afterEach(() => {
  wrapper?.unmount();
  vi.restoreAllMocks();
});

const parameters = () => ({
  span_um: 20,
  step_um: 2,
  preload_um: 10,
  cycles: 2,
  preferred_final_approach_sign: 1,
  validation_half_range_um: 4,
  maximum_uncertainty_um: 2.5,
  preload_margin_um: 1,
  maximum_residual_um: 2.5,
  maximum_drift_um: 2,
  minimum_prominence: 0.1,
  minimum_texture: 0.0001,
  minimum_signal: 0.02,
  maximum_saturation_fraction: 0.02,
  frame_timeout_s: 5,
  timeout_s: 900,
  maximum_frames: 1000,
  roi: { x: 0.25, y: 0.25, width: 0.5, height: 0.5 },
  mechanics_id: "current",
  optics_id: "current",
});
const saved = () => ({
  created_at: "2026-09-03T18:00:00Z",
  estimate: { backlash_um: 4, uncertainty_um: 2 },
  preload_um: 7,
  maximum_residual_um: 0.1,
});
async function mount(overrides = {}, read = null) {
  const state = {
    parameters: parameters(),
    readiness: { ready: true },
    calibration_status: { status: "not_calibrated", reason: "No saved Z calibration" },
    last_calibration: null,
    progress: { phase: "idle", frames: 0 },
    ...overrides,
  };
  const reader = read || vi.fn(async (_thing, property) => state[property]);
  wrapper = shallowMount(ZBacklashSettings, {
    global: {
      plugins: [createTestingPinia({ createSpy: vi.fn })],
      mocks: {
        thingDescription: () => ({ actions: { calibrate: {}, set_parameters: {} } }),
        readThingProperty: reader,
      },
    },
  });
  await flushPromises();
  return { state, reader };
}
const runButton = () =>
  wrapper.findAllComponents(ActionButton).find((button) => button.props("action") === "calibrate");

describe("standalone Z calibration settings", () => {
  it("loads saved parameters without applying them or starting motion", async () => {
    const { reader } = await mount();
    expect(wrapper.get('[data-z="span_um"]').element.value).toBe("20");
    expect(wrapper.text()).toContain("Not calibrated");
    expect(runButton().props("isDisabled")).toBe(true);
    expect(wrapper.vm.running).toBe(false);
    expect(reader.mock.calls.every(([thing]) => thing === "z_backlash")).toBe(true);
  });

  it("requires preparation, and dispatches only the standalone calibration", async () => {
    await mount();
    await wrapper.get('[data-z="prepared"]').setValue(true);
    expect(runButton().props("isDisabled")).toBe(false);
    expect(runButton().props("thing")).toBe("z_backlash");
    expect(runButton().props("submitData")).toEqual({ prepared: true });
    expect(runButton().props("modalProgress")).toBe(true);
    expect(runButton().props("canTerminate")).toBe(true);
  });

  it("blocks unreferenced motion without making up a zero", async () => {
    await mount({ readiness: { ready: false, reason: "No operator reference" } });
    await wrapper.get('[data-z="prepared"]').setValue(true);
    expect(runButton().props("isDisabled")).toBe(true);
    expect(wrapper.text()).toContain("No operator reference");
  });

  it("requires saving edited parameters before a run, and clears confirmation", async () => {
    await mount();
    await wrapper.get('[data-z="prepared"]').setValue(true);
    await wrapper.get('[data-z="span_um"]').setValue("24");
    expect(wrapper.vm.prepared).toBe(false);
    expect(runButton().props("isDisabled")).toBe(true);
    const save = wrapper
      .findAllComponents(ActionButton)
      .find((button) => button.props("action") === "set_parameters");
    expect(save.props("submitData").parameters.span_um).toBe(24);
    expect(save.props("isDisabled")).toBe(false);
  });

  it.each(["", 0, 21, NaN, Infinity])("rejects invalid sampling span %s", async (value) => {
    await mount();
    wrapper.vm.draft.span_um = value;
    await flushPromises();
    expect(runButton().props("isDisabled")).toBe(true);
  });

  it("converts ROI percentages and rejects a region extending outside the image", async () => {
    await mount();
    await wrapper.get('[data-roi="x"]').setValue("60");
    expect(wrapper.vm.draft.roi.x).toBe(0.6);
    expect(wrapper.vm.valid).toBe(false);
    expect(runButton().props("isDisabled")).toBe(true);
  });

  it("shows the saved result and its compatibility separately from readiness", async () => {
    await mount({
      last_calibration: saved(),
      calibration_status: { status: "incompatible", reason: "Optics changed" },
    });
    expect(wrapper.text()).toContain("4.00 µm");
    expect(wrapper.text()).toContain("7.00 µm");
    expect(wrapper.text()).toContain("Incompatible");
    expect(wrapper.text()).toContain("Optics changed");
  });

  it("does not claim a new calibration after an error", async () => {
    await mount({ last_calibration: saved() });
    runButton().vm.$emit("error", new Error("Focus curve rejected"));
    await flushPromises();
    expect(wrapper.text()).toContain("Focus curve rejected");
    expect(wrapper.vm.result).toEqual(saved());
    expect(wrapper.vm.prepared).toBe(false);
  });

  it("refreshes the server result after completion", async () => {
    const { state } = await mount();
    state.last_calibration = saved();
    state.calibration_status = { status: "valid" };
    runButton().vm.$emit("response", {});
    await flushPromises();
    expect(wrapper.vm.result).toEqual(saved());
    expect(wrapper.text()).toContain("Valid");
    expect(wrapper.vm.running).toBe(false);
  });

  it("keeps motion unavailable on failed initial reads", async () => {
    await mount({}, vi.fn().mockRejectedValue(new Error("offline")));
    expect(wrapper.text()).toContain("offline");
    expect(runButton()).toBeUndefined();
  });
});
