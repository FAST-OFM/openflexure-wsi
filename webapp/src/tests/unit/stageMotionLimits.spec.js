import { shallowMount, flushPromises } from "@vue/test-utils";
import { createTestingPinia } from "@pinia/testing";
import { describe, it, expect, vi, afterEach } from "vitest";
import StageMotionLimits from "../../components/tabContentComponents/settingsComponents/stageMotionLimits.vue";
import ActionButton from "../../components/labThingsComponents/actionButton.vue";
import { manualStageStep } from "../../js_utils/stageControlPreferences.js";
import { eventBus } from "../../eventBus.js";

let wrapper;
afterEach(() => {
  wrapper?.unmount();
  vi.restoreAllMocks();
});
const values = () =>
  Object.fromEntries(
    ["x", "y", "z"].map((axis) => [
      axis,
      {
        travel_limit_enabled: true,
        min_mm: -10,
        max_mm: 20,
        single_move_limit_enabled: true,
        max_move_mm: axis === "z" ? 0.05 : 1,
      },
    ]),
  );
const hardware = () => ({
  axes: Object.fromEntries(
    ["x", "y", "z"].map((axis) => [
      axis,
      { enabled: true, units_per_mm: 1000, max_move_mm: axis === "z" ? 0.05 : 1 },
    ]),
  ),
});
async function mount(read) {
  wrapper = shallowMount(StageMotionLimits, {
    global: {
      plugins: [createTestingPinia({ createSpy: vi.fn })],
      mocks: {
        thingDescription: () => ({ actions: { set_motion_limits: {} } }),
        readThingProperty:
          read ||
          vi.fn(async (_thing, property) => (property === "motion_limits" ? values() : hardware())),
      },
    },
  });
  await flushPromises();
  return wrapper.findComponent(ActionButton);
}

describe("shared software movement limits", () => {
  it("loads server settings in microns without applying them", async () => {
    const button = await mount();
    expect(wrapper.get('[data-limit="x-min"]').element.value).toBe("-10000");
    expect(wrapper.get('[data-limit="z-step"]').element.value).toBe("50");
    expect(button.props("action")).toBe("set_motion_limits");
    expect(button.props("submitData").limits).toEqual(values());
    expect(button.props("isDisabled")).toBe(false);
    expect(wrapper.vm.saving).toBe(false);
  });

  it("requires confirmation for either disabled guard, independently per axis", async () => {
    const button = await mount();
    await wrapper.get('[data-limit="z-single"]').setValue(false);
    expect(button.props("isDisabled")).toBe(true);
    expect(button.props("submitData").limits.z.travel_limit_enabled).toBe(true);
    await wrapper.setData({ confirmed: true });
    expect(button.props("isDisabled")).toBe(false);
    expect(button.props("submitData").confirm_disable).toBe(true);
    await wrapper.get('[data-limit="x-travel"]').setValue(false);
    expect(wrapper.vm.confirmed).toBe(false);
    expect(button.props("isDisabled")).toBe(true);
    expect(button.props("submitData").limits.x.single_move_limit_enabled).toBe(true);
    expect(wrapper.get('[data-limit="x-min"]').attributes("disabled")).toBeDefined();
  });

  it.each(["", 0, -1, NaN, Infinity])("rejects invalid single-move value %s", async (bad) => {
    const button = await mount();
    wrapper.vm.draft.z.max_move_um = bad;
    await flushPromises();
    expect(button.props("isDisabled")).toBe(true);
    expect(button.props("submitData")).toEqual({});
  });

  it("rejects inverted or zero-excluding travel ranges", async () => {
    const button = await mount();
    wrapper.vm.draft.x.min_um = 1;
    await flushPromises();
    expect(button.props("isDisabled")).toBe(true);
    wrapper.vm.draft.x.min_um = 0;
    wrapper.vm.draft.x.max_um = 0;
    await flushPromises();
    expect(button.props("isDisabled")).toBe(true);
  });

  it("converts user values and updates consumers only after acknowledged save", async () => {
    const button = await mount();
    const emit = vi.spyOn(eventBus, "emit");
    await wrapper.get('[data-limit="y-step"]').setValue("1200");
    expect(button.props("submitData").limits.y.max_move_mm).toBe(1.2);
    expect(emit).not.toHaveBeenCalled();
    button.vm.$emit("submit");
    await flushPromises();
    expect(wrapper.find("fieldset").attributes("disabled")).toBeDefined();
    button.vm.$emit("response", { status: "completed" });
    button.vm.$emit("finished");
    await flushPromises();
    expect(emit).toHaveBeenCalledWith("stageMotionLimitsChanged");
    expect(wrapper.text()).toContain("saved on the microscope");
    expect(wrapper.vm.saving).toBe(false);
  });

  it("does not invent defaults when the server read fails", async () => {
    await mount(vi.fn().mockRejectedValue(new Error("offline")));
    expect(wrapper.text()).toContain("Nothing was changed");
    expect(wrapper.findComponent(ActionButton).exists()).toBe(false);
  });

  it("reports a failed save without announcing success", async () => {
    const button = await mount();
    button.vm.$emit("error", "Stop motion before changing limits");
    await flushPromises();
    expect(wrapper.text()).toContain("Stop motion before changing limits");
    expect(wrapper.text()).not.toContain("saved on the microscope");
  });

  it("manual validator removes only the explicitly disabled single limit", () => {
    const axis = { units_per_mm: 1000, max_move_mm: 0.05 };
    expect(manualStageStep(axis, 0.1).valid).toBe(false);
    expect(manualStageStep({ ...axis, single_move_limit_enabled: false }, 0.1).valid).toBe(true);
    expect(manualStageStep({ ...axis, single_move_limit_enabled: false }, 0).valid).toBe(false);
    expect(manualStageStep({ ...axis, single_move_limit_enabled: false }, 0.0005).valid).toBe(
      false,
    );
  });
});
