import { shallowMount, flushPromises } from "@vue/test-utils";
import { createTestingPinia } from "@pinia/testing";
import { describe, it, expect, vi, afterEach } from "vitest";
import RGFocusSettings from "../../components/tabContentComponents/settingsComponents/cameraSettingsComponents/rgFocusSettings.vue";
import ActionButton from "../../components/labThingsComponents/actionButton.vue";

let wrapper;
afterEach(() => {
  wrapper?.unmount();
  vi.restoreAllMocks();
});

function readiness(status, changes = {}) {
  return {
    calibration_ready: true,
    measure_ready: true,
    autofocus_ready: status?.status === "valid",
    commissioning_match: true,
    policy_state: "commissioned",
    expected_policy_sha256: "1234567890abcdef",
    expected_policy: {
      measurement_domain: "processed-jpeg-rgb8",
      core: {
        patch_size_px: 64,
        patch_stride_px: 32,
        minimum_patch_count: 10,
        minimum_patch_signal: 16,
        jpeg8_saturation_level: 250,
      },
    },
    geometry: {
      expected: {
        source_image_size: [1014, 760],
        processing_roi: [102, 0, 810, 760],
        image_size: [810, 760],
      },
    },
    expected_parameters: {
      calibration_parameters: { z_offsets_um: [-8, -6, -4, -2, 0, 2, 4, 6, 8] },
      control_parameters: { maximum_iterations: 2, maximum_total_correction_um: 8 },
    },
    mismatches: [],
    ...changes,
  };
}

async function mount(status, currentReadiness = readiness(status)) {
  wrapper = shallowMount(RGFocusSettings, {
    global: {
      plugins: [
        createTestingPinia({
          createSpy: vi.fn,
          initialState: { settings: { baseUri: "http://pi/api/v3" } },
        }),
      ],
      mocks: {
        thingDescription: () => ({
          actions: {
            apply_demo_focus_contract: {},
            calibrate: {},
            measure: {},
            autofocus: {},
          },
        }),
        readThingProperty: vi.fn(
          async (_thing, property) =>
            ({
              approach_parameters: { preload_um: 8, approach_sign: 1 },
              calibration_status: status,
              readiness: currentReadiness,
            })[property],
        ),
      },
    },
  });
  await flushPromises();
}

const button = (action) =>
  wrapper.findAllComponents(ActionButton).find((item) => item.props("action") === action);

describe("tissue R/G focus settings", () => {
  it("loads and saves the shared approach without invoking a calibration", async () => {
    await mount({ status: "candidate" });
    expect(button("set_approach_parameters").props("submitData")).toEqual({
      parameters: { preload_um: 8, approach_sign: 1 },
    });
    expect(button("set_approach_parameters").props("isDisabled")).toBe(false);
    await wrapper.get('[data-rg-focus="approach-settings"] input').setValue(6);
    expect(button("set_approach_parameters").props("submitData").parameters.preload_um).toBe(6);
    expect(wrapper.vm.running).toBe(false);
  });
  it("shows a candidate reason and refuses autofocus while retaining no-motion measurement", async () => {
    await mount({ status: "candidate", reason: "two-sided approach failed" });
    await wrapper.get('[data-rg-focus="prepared"]').setValue(true);
    expect(wrapper.text()).toContain("candidate only");
    expect(wrapper.text()).toContain("two-sided approach failed");
    expect(button("measure").props("isDisabled")).toBe(false);
    expect(button("autofocus").props("isDisabled")).toBe(true);
  });

  it("enables only the R/G autofocus action for a valid compatible profile", async () => {
    await mount({ status: "valid" });
    await wrapper.get('[data-rg-focus="prepared"]').setValue(true);
    expect(button("autofocus").props()).toMatchObject({
      thing: "rg_focus",
      submitData: { prepared: true },
      canTerminate: true,
      modalProgress: true,
      isDisabled: false,
    });
  });

  it("shows the frozen JPEG contract and gates every hardware action from readiness", async () => {
    const state = readiness(
      { status: "not_calibrated" },
      {
        calibration_ready: false,
        measure_ready: false,
        autofocus_ready: false,
        commissioning_match: false,
        policy_state: "dirty",
        mismatches: [{ code: "camera_binding", path: "camera" }],
      },
    );
    await mount({ status: "not_calibrated" }, state);
    await wrapper.get('[data-rg-focus="prepared"]').setValue(true);
    expect(wrapper.get('[data-rg-focus="contract"]').text()).toContain("processed-jpeg-rgb8");
    expect(wrapper.get('[data-rg-focus="contract"]').text()).toContain("102, 0, 810, 760");
    expect(wrapper.get('[data-rg-focus="contract"]').text()).toContain("camera_binding");
    expect(button("apply_demo_focus_contract").props("isDisabled")).toBe(false);
    expect(button("calibrate").props("isDisabled")).toBe(true);
    expect(button("measure").props("isDisabled")).toBe(true);
    expect(button("autofocus").props("isDisabled")).toBe(true);
  });

  it("clears operator preparation when the contract is changed", async () => {
    await mount({ status: "valid" });
    await wrapper.get('[data-rg-focus="prepared"]').setValue(true);
    button("apply_demo_focus_contract").vm.$emit("submit");
    await flushPromises();
    expect(wrapper.vm.prepared).toBe(false);
    expect(wrapper.vm.readiness).toBe(null);
  });

  it("renders the fresh tissue overlay returned by the completed action", async () => {
    await mount({ status: "valid" });
    button("measure").vm.$emit("completed", {
      status: "ready",
      data_path: "run id",
      iterations: [],
    });
    await flushPromises();
    expect(wrapper.get('[data-rg-focus="result"] img').attributes("src")).toBe(
      "http://pi/api/v3/data/rg_focus/run%20id/iteration-01-overlay.png",
    );
    expect(wrapper.vm.prepared).toBe(false);
  });

  it("exposes cancellation and documents the finite verification stages", async () => {
    await mount({ status: "valid" });
    expect(button("calibrate").props("canTerminate")).toBe(true);
    expect(button("measure").props("canTerminate")).toBe(true);
    expect(button("autofocus").props("canTerminate")).toBe(true);
    expect(wrapper.text()).toContain("independent verification pair");
    expect(wrapper.text()).toContain("saved as candidate");
  });
});
