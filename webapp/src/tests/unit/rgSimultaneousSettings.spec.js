import { shallowMount, flushPromises } from "@vue/test-utils";
import { createTestingPinia } from "@pinia/testing";
import { describe, it, expect, vi } from "vitest";
import RGSimultaneousSettings from "../../components/tabContentComponents/settingsComponents/cameraSettingsComponents/rgSimultaneousSettings.vue";
import ActionButton from "../../components/labThingsComponents/actionButton.vue";

function mountSettings({ available = true } = {}) {
  const values = {
    status: { status: "valid", reason: "spectral holdout passed", enabled: false },
    focus_status: { status: "valid", reason: "focus holdouts passed", enabled: false },
    readiness: { ready: true },
    focus_readiness: { ready: true },
    focus_parameters: {
      focus_plane_roi: [350, 350, 700, 700],
      core: { maximum_patch_count: 16 },
    },
  };
  return shallowMount(RGSimultaneousSettings, {
    global: {
      plugins: [
        createTestingPinia({
          createSpy: vi.fn,
          initialState: { settings: { baseUri: "http://pi/api/v3" } },
        }),
      ],
      mocks: {
        thingDescription: () =>
          available
            ? {
                actions: {
                  calibrate: {},
                  calibrate_focus: {},
                  set_focus_parameters: {},
                  set_enabled: {},
                },
              }
            : undefined,
        readThingProperty: vi.fn((_thing, property) => Promise.resolve(values[property])),
      },
    },
  });
}

describe("simultaneous R/G settings", () => {
  it("keeps both calibration owners visible and exposes one enable switch", async () => {
    const wrapper = mountSettings();
    await flushPromises();

    expect(wrapper.text()).toContain("Simultaneous R+G autofocus");
    expect(wrapper.text()).toContain("spectral holdout passed");
    expect(wrapper.text()).toContain("focus holdouts passed");
    const actions = wrapper.findAllComponents(ActionButton);
    expect(actions.map((button) => button.props("action"))).toEqual([
      "calibrate",
      "calibrate_focus",
      "measure_focus",
      "set_focus_parameters",
      "set_enabled",
    ]);
    expect(actions.at(3).props()).toMatchObject({
      submitLabel: "Save focus processing area",
      submitData: {
        parameters: {
          focus_plane_roi: [350, 350, 700, 700],
          core: { maximum_patch_count: 16 },
        },
      },
    });
    expect(actions.at(-1).props()).toMatchObject({
      submitLabel: "Enable simultaneous R+G",
      submitData: { enabled: true },
      isDisabled: false,
    });
  });

  it("stays absent when the independent server component is not installed", async () => {
    const wrapper = mountSettings({ available: false });
    await flushPromises();
    expect(wrapper.find("section").exists()).toBe(false);
    expect(wrapper.findAllComponents(ActionButton)).toHaveLength(0);
  });
});
