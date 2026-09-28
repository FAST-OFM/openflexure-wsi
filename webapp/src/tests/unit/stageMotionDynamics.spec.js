import { shallowMount, flushPromises } from "@vue/test-utils";
import { createTestingPinia } from "@pinia/testing";
import { beforeEach, afterEach, describe, expect, it, vi } from "vitest";
import StageMotionDynamics from "../../components/tabContentComponents/settingsComponents/stageMotionDynamics.vue";
import ActionButton from "../../components/labThingsComponents/actionButton.vue";
import { eventBus } from "../../eventBus.js";

const dynamics = {
  x: { speed_mm_s: 0.25, accel_mm_s2: 2, settle_ms: 750, move_timeout_s: 15 },
  y: { speed_mm_s: 0.25, accel_mm_s2: 2, settle_ms: 750, move_timeout_s: 15 },
  z: { speed_mm_s: 0.02, accel_mm_s2: 0.1, settle_ms: 1000, move_timeout_s: 15 },
};
const hardware = {
  axes: {
    x: { enabled: true },
    y: { enabled: true },
    z: { enabled: true },
  },
};

let wrapper;
let read;

beforeEach(() => {
  read = vi.fn(async (_thing, property) =>
    property === "motion_dynamics" ? structuredClone(dynamics) : structuredClone(hardware),
  );
  wrapper = shallowMount(StageMotionDynamics, {
    global: {
      plugins: [createTestingPinia({ createSpy: vi.fn })],
      mocks: {
        thingDescription: () => ({ actions: { set_motion_dynamics: {} } }),
        readThingProperty: read,
      },
    },
  });
});

afterEach(() => wrapper?.unmount());

describe("stage automatic motion settings", () => {
  it("loads one complete profile and submits all axes atomically", async () => {
    await flushPromises();
    expect(read).toHaveBeenCalledWith("stage", "motion_dynamics", true);
    expect(wrapper.vm.valid).toBe(true);
    await wrapper.get('[data-motion="x-speed"]').setValue("1");
    await wrapper.get('[data-motion="x-accel"]').setValue("10");
    await wrapper.get('[data-motion="x-settle"]').setValue("100");
    const button = wrapper.getComponent(ActionButton);
    expect(button.props("submitData")).toEqual({
      dynamics: {
        ...dynamics,
        x: { speed_mm_s: 1, accel_mm_s2: 10, settle_ms: 100, move_timeout_s: 15 },
      },
    });
  });

  it("rejects invalid values before invoking the server", async () => {
    await flushPromises();
    await wrapper.get('[data-motion="z-settle"]').setValue("5001");
    expect(wrapper.vm.valid).toBe(false);
    expect(wrapper.getComponent(ActionButton).props("isDisabled")).toBe(true);
    expect(wrapper.text()).toContain("Settle must be 0–5000 ms");
  });

  it("reloads consumers only after a successful save", async () => {
    const emit = vi.spyOn(eventBus, "emit");
    await flushPromises();
    await wrapper.vm.onSaved();
    expect(emit).toHaveBeenCalledWith("stageMotionDynamicsChanged");
    expect(wrapper.text()).toContain("Motion settings saved");
  });

  it("does not render against a stage without the action", async () => {
    wrapper.unmount();
    read.mockClear();
    wrapper = shallowMount(StageMotionDynamics, {
      global: {
        plugins: [createTestingPinia({ createSpy: vi.fn })],
        mocks: {
          thingDescription: () => ({ actions: {} }),
          readThingProperty: read,
        },
      },
    });
    await flushPromises();
    expect(wrapper.html()).toBe("<!--v-if-->");
    expect(read).not.toHaveBeenCalled();
  });
});
