import { shallowMount, flushPromises } from "@vue/test-utils";
import { createTestingPinia } from "@pinia/testing";
import { describe, it, expect, vi, afterEach, beforeEach } from "vitest";
import StageButtons from "../../components/tabContentComponents/controlComponents/stageControlButtons.vue";
import PositionControl from "../../components/tabContentComponents/controlComponents/positionControl.vue";
import StagePreferences from "../../components/tabContentComponents/settingsComponents/stageControlSettings.vue";
import { useSettingsStore } from "../../stores/settings.js";
import { manualStageStep } from "../../js_utils/stageControlPreferences.js";
import { eventBus } from "../../eventBus.js";
import CameraFocusStep from "../../components/modalComponents/calibrationWizardComponents/cameraCalibrationSteps/cameraFocusStep.vue";
import CSMFocusStep from "../../components/modalComponents/calibrationWizardComponents/csmSteps/focusStep.vue";
import CSMSettings from "../../components/tabContentComponents/settingsComponents/CSMSettingsComponents/CSMCalibrationSettings.vue";
import StageSettings from "../../components/tabContentComponents/settingsComponents/stageSettings.vue";
import CompactHelp from "../../components/genericComponents/compactHelp.vue";

let wrapper;
beforeEach(() => vi.useFakeTimers());
afterEach(() => {
  wrapper?.unmount();
  vi.clearAllTimers();
  vi.useRealTimers();
});
function buttons(moveStep, extra = {}) {
  wrapper = shallowMount(StageButtons, {
    props: {
      bounded: true,
      moveStep,
      stepUnits: { x: 50, y: 50, z: 5 },
      ...extra,
    },
    global: {
      plugins: [createTestingPinia({ createSpy: vi.fn })],
      mocks: { invokeAction: vi.fn() },
    },
  });
  return wrapper;
}

describe("bounded held-arrow controls", () => {
  it("shows busy through completion, ignores early taps and accepts the next ready tap", async () => {
    let complete;
    const move = vi.fn(() => new Promise((resolve) => (complete = resolve)));
    buttons(move);
    const arrow = wrapper.get("#right-button");
    arrow.element.setPointerCapture = vi.fn();
    await arrow.trigger("pointerdown", { button: 0, pointerId: 1 });
    await wrapper.setProps({ busy: true });
    expect(wrapper.get("#right-button").attributes("aria-disabled")).toBe("true");
    // Busy must preserve release delivery, or one tap can become a runaway hold.
    expect(arrow.attributes("disabled")).toBeUndefined();
    await arrow.trigger("pointerup");
    await arrow.trigger("pointerdown", { button: 0, pointerId: 1 });
    await arrow.trigger("pointerup");
    expect(move).toHaveBeenCalledTimes(1);
    await wrapper.setProps({ busy: false });
    complete(true);
    await flushPromises();
    expect(wrapper.get("#right-button").attributes("aria-disabled")).toBe("false");
    await arrow.trigger("pointerdown", { button: 0, pointerId: 1 });
    await arrow.trigger("pointerup");
    complete(true);
    await flushPromises();
    await vi.advanceTimersByTimeAsync(1000);
    expect(move).toHaveBeenCalledTimes(2);
  });
  it("does not cancel an existing hold when its own step becomes busy", async () => {
    let complete;
    const move = vi
      .fn()
      .mockImplementationOnce(() => new Promise((resolve) => (complete = resolve)))
      .mockResolvedValueOnce(false);
    buttons(move);
    const held = wrapper.vm.beginHold(0, 0, 1);
    await wrapper.setProps({ busy: true });
    expect(wrapper.vm.held).toBe(true);
    await vi.advanceTimersByTimeAsync(1500);
    expect(move).toHaveBeenCalledTimes(1);
    await wrapper.setProps({ busy: false });
    complete(true);
    await flushPromises();
    await vi.advanceTimersByTimeAsync(100);
    await held;
    expect(move).toHaveBeenCalledTimes(2);
  });
  it("waits for completion and never queues another move on release", async () => {
    let complete;
    const move = vi.fn(
      () =>
        new Promise((resolve) => {
          complete = resolve;
        }),
    );
    buttons(move);
    const held = wrapper.vm.beginHold(1, 0, 0);
    await vi.advanceTimersByTimeAsync(2000);
    expect(move).toHaveBeenCalledTimes(1);
    expect(move).toHaveBeenCalledWith({ x: 50, y: 0, z: 0 });
    wrapper.vm.jogStop();
    complete(true);
    await held;
    await vi.advanceTimersByTimeAsync(2000);
    expect(move).toHaveBeenCalledTimes(1);
  });
  it("repeats only after an acknowledged step and uses the separate Z step", async () => {
    const move = vi.fn().mockResolvedValueOnce(true).mockResolvedValueOnce(false);
    buttons(move);
    const held = wrapper.vm.beginHold(0, 0, -1);
    await flushPromises();
    expect(move).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(100);
    await held;
    expect(move).toHaveBeenCalledTimes(2);
    expect(move).toHaveBeenLastCalledWith({ x: 0, y: 0, z: -5 });
    expect(wrapper.vm.held).toBe(false);
  });
  it("does not retry an ambiguous failed request", async () => {
    const move = vi.fn().mockResolvedValue(false);
    buttons(move);
    await wrapper.vm.beginHold(1, 0, 0);
    await vi.advanceTimersByTimeAsync(1000);
    expect(move).toHaveBeenCalledTimes(1);
  });
  it("stops on focus loss before the next request", async () => {
    const move = vi.fn().mockResolvedValue(true);
    buttons(move);
    const held = wrapper.vm.beginHold(1, 0, 0);
    await flushPromises();
    window.dispatchEvent(new Event("blur"));
    await held;
    await vi.advanceTimersByTimeAsync(1000);
    expect(move).toHaveBeenCalledTimes(1);
  });
  it("rejects disabled controls and disabled axes", async () => {
    const move = vi.fn();
    buttons(move, { disabled: true });
    await wrapper.vm.beginHold(1, 0, 0);
    await wrapper.setProps({ disabled: false, axesEnabled: { x: true, y: true, z: false } });
    await wrapper.vm.beginHold(0, 0, 1);
    expect(move).not.toHaveBeenCalled();
    expect(wrapper.get("#focus-in-button").attributes("disabled")).toBeDefined();
  });
  it("unmount stops a pending hold without another command", async () => {
    let complete;
    const move = vi.fn(
      () =>
        new Promise((resolve) => {
          complete = resolve;
        }),
    );
    buttons(move);
    const held = wrapper.vm.beginHold(1, 0, 0);
    wrapper.unmount();
    wrapper = null;
    complete(true);
    await held;
    expect(move).toHaveBeenCalledTimes(1);
  });
});

function position(controller, hasControls = true, props = {}) {
  const read = vi.fn(async (_thing, field) => {
    if (field === "controller_state")
      return (
        controller && {
          ...controller,
          position: controller.position ?? { x: 0, y: 0, z: 0 },
          motion_idle:
            controller.motion_idle ??
            (controller.live_velocity === 0 &&
              controller.print_time <= controller.estimated_print_time),
        }
      );
    if (field === "hardware_settings")
      return {
        allow_motion: true,
        allow_operator_controls: true,
        manual_settle_ms: 0,
        ui_repeat_delay_ms: 20,
        axes: Object.fromEntries(
          ["x", "y", "z"].map((axis) => [
            axis,
            {
              enabled: true,
              ui_step_mm: axis === "z" ? 0.005 : 0.05,
              units_per_mm: 1000,
              max_move_mm: axis === "z" ? 0.05 : 1,
              speed_mm_s: axis === "z" ? 0.02 : 0.25,
              settle_ms: axis === "z" ? 1000 : 750,
            },
          ]),
        ),
      };
    return { x: 0, y: 0, z: 0 };
  });
  wrapper = shallowMount(PositionControl, {
    props,
    global: {
      plugins: [createTestingPinia({ createSpy: vi.fn })],
      mocks: {
        thingDescription: () => ({
          actions: hasControls ? { enable_motors: {}, move_manual: {} } : {},
        }),
        readThingProperty: read,
        invokeAction: vi.fn(),
        modalError: vi.fn(),
        pollUntilComplete: vi.fn(),
      },
    },
  });
  return read;
}

describe("native position panel", () => {
  it("uses the position in the physical controller snapshot without a duplicate read", async () => {
    const read = position({
      state: "ready",
      motion_idle: true,
      reference_valid: true,
      enabled: { stepper_x: true, stepper_y: true, stepper_z: true },
      position: { x: 11, y: -22, z: 3 },
    });
    await flushPromises();
    expect(wrapper.vm.setPosition).toEqual({ x: 11, y: -22, z: 3 });
    expect(read.mock.calls.filter(([, field]) => field === "position")).toHaveLength(0);
  });

  it("keeps ready controls compact, with step details in a tooltip", async () => {
    position({
      state: "ready",
      motion_idle: true,
      reference_valid: true,
      enabled: { stepper_x: true, stepper_y: true, stepper_z: true },
    });
    await flushPromises();
    expect(wrapper.text()).toContain("Motors: on · Zero: set");
    expect(wrapper.text()).not.toMatch(/Bounded steps|Ready to move|Manual settle|Step:/);
    expect(wrapper.findComponent(StageButtons).attributes("title")).toBe(
      "X step: 50 µm · Y step: 50 µm · Z step: 5 µm",
    );
    expect(wrapper.vm.invokeAction).not.toHaveBeenCalled();
  });

  it("still displays real controller faults and invalid step settings", async () => {
    position({
      state: "ready",
      motion_idle: true,
      reference_valid: false,
      fault: "Motion acknowledgement lost",
    });
    await flushPromises();
    const fault = wrapper.findComponent(CompactHelp);
    expect(fault.props("label")).toBe("Stage not ready");
    expect(fault.props("text")).toBe("Motion acknowledgement lost");
    const store = useSettingsStore(wrapper.vm.$pinia);
    store.stageNavigationStepsMm[store.baseUri] = { z: 50 };
    await wrapper.vm.$nextTick();
    expect(wrapper.findAllComponents(CompactHelp).at(-1).props("label")).toBe(
      "Check movement steps",
    );
    expect(wrapper.findComponent(StageButtons).props("disabled")).toBe(true);
  });

  it("uses manual motion and keeps arrows busy through completion and readback", async () => {
    const ready = { state: "ready", motion_idle: true, reference_valid: true };
    const read = position(ready);
    await flushPromises();
    wrapper.vm.invokeAction.mockResolvedValue({ data: { href: "/move/1" } });
    let finished;
    wrapper.vm.pollUntilComplete.mockImplementation((_url, _ongoing, final) => {
      finished = final;
    });
    const move = wrapper.vm.boundedStep({ x: 50, y: 0, z: 0 });
    await flushPromises();
    expect(wrapper.findComponent(StageButtons).props("busy")).toBe(true);
    expect(wrapper.text()).toContain("Moving…");
    expect(wrapper.vm.invokeAction).toHaveBeenCalledWith(
      "stage",
      "move_manual",
      { x: 50, y: 0, z: 0, relative: true },
      false,
    );
    expect(await wrapper.vm.boundedStep({ x: 50, y: 0, z: 0 })).toBe(false);
    expect(wrapper.vm.invokeAction).toHaveBeenCalledTimes(1);
    let readback;
    read.mockImplementationOnce(() => new Promise((resolve) => (readback = resolve)));
    const finish = finished({ data: { status: "completed" } });
    await flushPromises();
    expect(wrapper.findComponent(StageButtons).props("busy")).toBe(true);
    readback({ ...ready, position: { x: 0, y: 0, z: 0 } });
    await finish;
    expect(await move).toBe(true);
    await wrapper.vm.$nextTick();
    expect(wrapper.findComponent(StageButtons).props("busy")).toBe(false);
    expect(wrapper.vm.canMove).toBe(true);
    const absolute = wrapper
      .findAllComponents({ name: "ActionButton" })
      .find((button) => button.props("submitLabel") === "Move");
    expect(absolute.props("action")).toBe("move_manual");
    expect(absolute.props("submitData")).toEqual({ x: 0, y: 0, z: 0, relative: false });
    expect(wrapper.findComponent(StageButtons).props("repeatDelayMs")).toBe(20);
    const next = wrapper.vm.boundedStep({ x: 50, y: 0, z: 0 });
    await flushPromises();
    expect(wrapper.vm.invokeAction).toHaveBeenCalledTimes(2);
    await finished({ data: { status: "completed" } });
    expect(await next).toBe(true);
  });
  it("uses the server idle verdict instead of exact floating-point velocity equality", async () => {
    position({
      state: "ready",
      motion_idle: true,
      live_velocity: -3.469446951953614e-18,
      reference_valid: true,
    });
    await flushPromises();
    expect(wrapper.vm.controllerIdle).toBe(true);
    expect(wrapper.vm.canMove).toBe(true);
    wrapper.vm.controller.motion_idle = false;
    expect(wrapper.vm.canMove).toBe(false);
  });
  it("uses the saved physical preferences, not stock Sangaboard numbers", async () => {
    position({
      state: "ready",
      live_velocity: 0,
      print_time: 1,
      estimated_print_time: 2,
      reference_valid: true,
    });
    await flushPromises();
    const store = useSettingsStore(wrapper.vm.$pinia);
    expect(wrapper.vm.stepUnits).toEqual({ x: 50, y: 50, z: 5 });
    store.navigationStepSize = { x: 999, y: 999, z: 999 };
    expect(wrapper.vm.stepUnits).toEqual({ x: 50, y: 50, z: 5 });
    store.stageNavigationStepsMm[store.baseUri] = { x: 0.025, y: 0.03, z: 0.002 };
    await wrapper.vm.$nextTick();
    expect(wrapper.findComponent(StageButtons).props("stepUnits")).toEqual({ x: 25, y: 30, z: 2 });
    expect(wrapper.vm.canMove).toBe(true);
    store.stageNavigationStepsMm[store.baseUri].z = 50;
    await wrapper.vm.$nextTick();
    expect(wrapper.vm.canMove).toBe(false);
    expect(wrapper.findComponent(StageButtons).props("disabled")).toBe(true);
  });
  it.each([false, true, "partial"])(
    "has exactly one motor toggle for state %s",
    async (enabled) => {
      position({
        state: "ready",
        live_velocity: 0,
        print_time: 1,
        estimated_print_time: 2,
        enabled: {
          stepper_x: enabled !== false,
          stepper_y: enabled === true,
          stepper_z: enabled === true,
        },
        reference_valid: false,
      });
      await flushPromises();
      const motors = wrapper
        .findAllComponents({ name: "ActionButton" })
        .filter((button) => ["enable_motors", "disable_motors"].includes(button.props("action")));
      expect(motors).toHaveLength(1);
      expect(motors[0].props("action")).toBe(
        enabled === false ? "enable_motors" : "disable_motors",
      );
    },
  );
  it("shows motor controls and explicit zero confirmation, without fabricating a position", async () => {
    const read = position({
      state: "ready",
      live_velocity: 0,
      print_time: 1,
      estimated_print_time: 2,
      enabled: { stepper_x: false, stepper_y: false, stepper_z: false },
      reference_valid: false,
    });
    await flushPromises();
    expect(wrapper.text()).toContain("Motors: off");
    expect(wrapper.vm.canMove).toBe(false);
    expect(read.mock.calls.some((args) => args[1] === "position")).toBe(false);
    const zero = wrapper
      .findAllComponents({ name: "ActionButton" })
      .find((button) => button.props("action") === "set_zero_position");
    expect(zero.props("requiresConfirmation")).toBe(true);
    expect(zero.props("submitData").initialise_controller).toBe(true);
    expect(zero.props("isDisabled")).toBe(true);
  });
  it("leaves stock stage controls unchanged for other drivers", async () => {
    position(null, false);
    await flushPromises();
    const actions = wrapper
      .findAllComponents({ name: "ActionButton" })
      .map((button) => button.props("action"));
    expect(actions).not.toContain("enable_motors");
    expect(actions).toContain("move_to_origin");
    expect(wrapper.findComponent(StageButtons).props("bounded")).toBe(false);
  });
});

describe("manual stage preferences", () => {
  const profile = {
    manual_settle_ms: 0,
    ui_repeat_delay_ms: 20,
    axes: Object.fromEntries(
      ["x", "y", "z"].map((axis) => [
        axis,
        {
          enabled: true,
          units_per_mm: 1000,
          ui_step_mm: axis === "z" ? 0.005 : 0.05,
          max_move_mm: axis === "z" ? 0.05 : 1,
          speed_mm_s: axis === "z" ? 0.02 : 0.25,
          settle_ms: axis === "z" ? 1000 : 750,
        },
      ]),
    ),
  };
  function preferences(bounded = true, read = vi.fn().mockResolvedValue(profile)) {
    wrapper = shallowMount(StagePreferences, {
      global: {
        plugins: [createTestingPinia({ createSpy: vi.fn })],
        mocks: {
          thingDescription: () => ({ actions: bounded ? { enable_motors: {} } : {} }),
          readThingProperty: read,
          invokeAction: vi.fn(),
        },
      },
    });
    return useSettingsStore(wrapper.vm.$pinia);
  }
  it("shows real profile defaults with units and does not send hardware actions", async () => {
    preferences();
    await flushPromises();
    expect(wrapper.get("#manual-step-x").element.value).toBe("50");
    expect(wrapper.get("#manual-step-y").element.value).toBe("50");
    expect(wrapper.get("#manual-step-z").element.value).toBe("5");
    expect(wrapper.text()).toContain("Z step (µm)");
    expect(wrapper.text()).toContain("0.02");
    expect(
      wrapper
        .findAllComponents(CompactHelp)
        .find((help) => help.props("label") === "Timing")
        .props("text"),
    ).toContain("Manual settle 0 ms");
    expect(wrapper.text()).toContain("Auto settle (ms)");
    expect(wrapper.vm.invokeAction).not.toHaveBeenCalled();
  });
  it("applies edits, isolates microscope origins and restores profile defaults", async () => {
    const store = preferences();
    await flushPromises();
    await wrapper.get("#manual-step-z").setValue(2);
    await wrapper.get("form").trigger("submit");
    const origin = store.baseUri;
    expect(store.stageNavigationStepsMm[origin]).toEqual({ x: 0.05, y: 0.05, z: 0.002 });
    expect(wrapper.text()).toContain("Applied to Control");
    store.baseUri = "http://another-microscope/api/v3";
    await flushPromises();
    expect(wrapper.get("#manual-step-z").element.value).toBe("5");
    store.baseUri = origin;
    await flushPromises();
    expect(wrapper.get("#manual-step-z").element.value).toBe("2");
    wrapper.vm.restoreDefaults();
    await wrapper.vm.$nextTick();
    expect(store.stageNavigationStepsMm[origin]).toBeUndefined();
    expect(wrapper.get("#manual-step-z").element.value).toBe("5");
    expect(wrapper.vm.invokeAction).not.toHaveBeenCalled();
  });
  it("accepts 1000 micron XY steps and rejects larger ones without changing Z", async () => {
    const store = preferences();
    await flushPromises();
    for (const axis of ["x", "y"]) {
      expect(wrapper.get("#manual-step-" + axis).attributes("max")).toBe("1000");
      await wrapper.get("#manual-step-" + axis).setValue(1000);
    }
    await wrapper.get("form").trigger("submit");
    expect(store.stageNavigationStepsMm[store.baseUri]).toEqual({ x: 1, y: 1, z: 0.005 });
    expect(wrapper.get("#manual-step-z").attributes("max")).toBe("50");
    for (const axis of ["x", "y"]) {
      wrapper.vm.draftUm[axis] = 1001;
      await wrapper.get("form").trigger("submit");
      expect(wrapper.vm.formError).toContain("Check " + axis.toUpperCase());
      expect(store.stageNavigationStepsMm[store.baseUri][axis]).toBe(1);
      wrapper.vm.draftUm[axis] = 1000;
    }
    expect(wrapper.vm.invokeAction).not.toHaveBeenCalled();
  });
  it.each([0, -5, 51, 0.5, ""])(
    "rejects invalid Z preference %s without changing the saved step",
    async (value) => {
      const store = preferences();
      await flushPromises();
      wrapper.vm.draftUm.z = value;
      await wrapper.get("form").trigger("submit");
      expect(wrapper.vm.formError).toContain("Check Z");
      expect(store.stageNavigationStepsMm[store.baseUri]).toBeUndefined();
      expect(wrapper.vm.invokeAction).not.toHaveBeenCalled();
    },
  );
  it("does not invent profile values when the hardware read fails", async () => {
    preferences(true, vi.fn().mockRejectedValue(new Error("offline")));
    await flushPromises();
    expect(wrapper.text()).toContain("Cannot read the stage motion profile");
    expect(wrapper.find("#manual-step-z").exists()).toBe(false);
  });
  it("preserves stock preferences for other stage drivers", async () => {
    const store = preferences(false);
    await flushPromises();
    expect(wrapper.text()).toContain("Single Move Step Size");
    expect(wrapper.find("#manual-step-z").exists()).toBe(false);
    expect(store.navigationStepSize).toEqual({ x: 200, y: 200, z: 50 });
    expect(wrapper.vm.readThingProperty).not.toHaveBeenCalled();
  });
  it.each([NaN, Infinity, null, "0.005", 0, -1, 0.0005, 0.051])(
    "fails closed on invalid persisted Z value %s",
    (value) => {
      expect(manualStageStep(profile.axes.z, value).valid).toBe(false);
    },
  );
  it("cancels a held button if the step preference changes", async () => {
    const move = vi.fn().mockResolvedValue(true);
    buttons(move);
    const held = wrapper.vm.beginHold(0, 0, 1);
    await flushPromises();
    await wrapper.setProps({ stepUnits: { x: 50, y: 50, z: 2 } });
    await held;
    await vi.advanceTimersByTimeAsync(1000);
    expect(move).toHaveBeenCalledTimes(1);
  });
});

describe("physical focus controls in calibration wizards", () => {
  it.each([CameraFocusStep, CSMFocusStep])(
    "reuses PositionControl without the old jog widget",
    (component) => {
      wrapper = shallowMount(component, {
        global: {
          stubs: {
            stepTemplateWithStream: { template: "<div><slot/><slot name='below-stream'/></div>" },
          },
        },
      });
      expect(wrapper.findComponent(PositionControl).props("focusOnly")).toBe(true);
      expect(wrapper.findComponent(StageButtons).exists()).toBe(false);
    },
  );

  it("uses saved Z preferences and does not register a second movement listener", async () => {
    const on = vi.spyOn(eventBus, "on");
    position({ state: "ready", motion_idle: true, reference_valid: true }, true, {
      focusOnly: true,
    });
    await flushPromises();
    expect(wrapper.findComponent(StageButtons).props("bounded")).toBe(true);
    expect(wrapper.findComponent(StageButtons).props("showDpad")).toBe(false);
    expect(on.mock.calls.map(([event]) => event)).not.toContain("globalMoveEvent");
    expect(on.mock.calls.map(([event]) => event)).not.toContain(
      "globalMoveInImageCoordinatesEvent",
    );
    on.mockRestore();
    const store = useSettingsStore();
    store.stageNavigationStepsMm[store.baseUri] = { x: 0.05, y: 0.05, z: 0.01 };
    await flushPromises();
    const controls = wrapper.findComponent(StageButtons);
    expect(controls.props("stepUnits").z).toBe(10);
    wrapper.vm.invokeAction.mockResolvedValue({ data: { href: "/move/test" } });
    wrapper.vm.pollUntilComplete.mockImplementation((_url, _progress, done) =>
      done({ data: { status: "completed" } }),
    );
    expect(await controls.props("moveStep")({ x: 0, y: 0, z: 10 })).toBe(true);
    expect(wrapper.vm.invokeAction).toHaveBeenCalledWith(
      "stage",
      "move_manual",
      { relative: true, x: 0, y: 0, z: 10 },
      false,
    );
  });

  it("does not display native values as microns when the profile is unavailable", async () => {
    position({ state: "ready", motion_idle: true, reference_valid: true });
    await flushPromises();
    await wrapper.setData({ hardware: null, setPosition: { x: 10, y: 20, z: 30 } });
    expect(wrapper.vm.displayCoordinate("z")).toBe("");
    expect(wrapper.vm.coordinateStep("z")).toBe("any");
    wrapper.vm.setCoordinate("z", "5");
    await flushPromises();
    expect(wrapper.vm.coordinatesValid).toBe(false);
    expect(wrapper.vm.canMove).toBe(false);
  });

  it("shows and accepts Position coordinates in physical units per axis", async () => {
    position({ state: "ready", motion_idle: true, reference_valid: true });
    await flushPromises();
    const hardware = JSON.parse(JSON.stringify(wrapper.vm.hardware));
    hardware.axes.z.units_per_mm = 2000;
    await wrapper.setData({ hardware, setPosition: { x: 0, y: 0, z: 20 } });
    expect(wrapper.vm.displayCoordinate("z")).toBe(10);
    expect(wrapper.text()).toContain("Z (µm)");
    wrapper.vm.setCoordinate("z", "1.5");
    expect(wrapper.vm.setPosition.z).toBe(3);
    wrapper.vm.setCoordinate("z", "");
    await flushPromises();
    expect(wrapper.vm.coordinatesValid).toBe(false);
    expect(wrapper.vm.invokeAction).not.toHaveBeenCalled();
  });
});

describe("physical calibration details", () => {
  it("converts both matrix components and handles unequal axis scales", async () => {
    wrapper = shallowMount(CSMSettings, {
      global: {
        mocks: {
          modalError: vi.fn(),
          thingDescription: () => ({ properties: { hardware_settings: {} } }),
          readThingProperty: vi.fn(
            async (_thing, property) =>
              ({
                image_to_stage_displacement_matrix: [
                  [0.5, 0],
                  [0, 4],
                ],
                image_resolution: [100, 200],
                hardware_settings: {
                  axes: { x: { units_per_mm: 1000 }, y: { units_per_mm: 4000 } },
                },
              })[property],
          ),
        },
      },
    });
    await wrapper.vm.updateDisplayedCSM();
    expect(wrapper.vm.csmFOV).toEqual([200, 50]);
    expect(wrapper.vm.csmRatio).toBe("1.000 / 0.500");
    expect(wrapper.text()).toContain("µm/px");
  });

  it("does not offer stock gear inversion on Moonraker", () => {
    wrapper = shallowMount(StageSettings, {
      global: {
        mocks: {
          thingDescription: () => ({ title: "Moonraker", properties: { hardware_settings: {} } }),
          thingAvailable: () => false,
        },
      },
    });
    expect(
      wrapper
        .findAllComponents(CompactHelp)
        .find((help) => help.props("label") === "Axis directions")
        .props("text"),
    ).toContain("direction_sign");
    expect(wrapper.html()).not.toContain('action="invert_axis_direction"');
  });
});
