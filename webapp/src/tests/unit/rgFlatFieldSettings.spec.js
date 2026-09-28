import { shallowMount, flushPromises } from "@vue/test-utils";
import { createTestingPinia } from "@pinia/testing";
import { describe, it, expect, vi, afterEach } from "vitest";
import RGFlatFieldSettings from "../../components/tabContentComponents/settingsComponents/cameraSettingsComponents/rgFlatFieldSettings.vue";
import ActionButton from "../../components/labThingsComponents/actionButton.vue";

let wrapper;
afterEach(() => {
  wrapper?.unmount();
  wrapper = undefined;
});
const parameters = {
  measurement_domain: "processed-jpeg-rgb8",
  processing_roi: [102, 0, 810, 760],
  dark_frames: 4,
  average_frames: 4,
  validation_frames: 3,
  frame_timeout_s: 5,
  timeout_s: 180,
  smoothing_sigma_px: 16,
  minimum_signal_dn: 8,
  maximum_gain: 4,
  maximum_mask_fraction: 0.02,
  maximum_saturation_fraction: 0.001,
  saturation_level_fraction: 0.98,
  maximum_dark_signal_dn: 32,
  maximum_field_texture: 0.04,
  maximum_residual_cv: 0.05,
  maximum_drift_fraction: 0.05,
  maximum_spatial_residual_fraction: 0.05,
  optics_id: "current",
  illumination_id: "current",
};
const profile = {
  method: "phase_matched_processed_jpeg_measured_dark",
  id: "saved-map",
  created_at: "2026-09-03T18:00:00Z",
  validation: { before: { cv: [0.2] }, after: { cv: [0.01] } },
};
async function mount(overrides = {}) {
  const state = {
    parameters: structuredClone(parameters),
    jpeg_measurement_configuration: {
      measurement_space: "processed-jpeg-rgb8",
      geometry: { image_size: [1014, 760] },
    },
    readiness: { ready: true },
    calibration_status: { red: { status: "not_calibrated" }, green: { status: "not_calibrated" } },
    profiles: {},
    ...overrides,
  };
  const read = vi.fn(async (_thing, name) => state[name]);
  wrapper = shallowMount(RGFlatFieldSettings, {
    global: {
      plugins: [createTestingPinia({ createSpy: vi.fn })],
      mocks: { thingDescription: () => ({ actions: { calibrate: {} } }), readThingProperty: read },
    },
  });
  await flushPromises();
  return { state, read };
}
const buttons = () => wrapper.findAllComponents(ActionButton);
const calibrate = () => buttons().filter((b) => b.props("action") === "calibrate");
describe("R/G flat-field in native camera calibration", () => {
  it("does not start anything on mount and requires empty-field confirmation", async () => {
    await mount();
    expect(wrapper.emitted("actionStarted")).toBeUndefined();
    expect(calibrate()).toHaveLength(3);
    expect(calibrate().every((b) => b.props("isDisabled"))).toBe(true);
    expect(wrapper.text()).not.toContain("WHITE calibration");
    await wrapper.get('[data-rg="prepared"]').setValue(true);
    expect(calibrate().every((b) => !b.props("isDisabled"))).toBe(true);
    expect(calibrate()[0].props("submitData")).toEqual({ prepared: true, modes: ["red", "green"] });
    expect(calibrate()[1].props("submitData").modes).toEqual(["red"]);
    expect(calibrate()[2].props("submitData").modes).toEqual(["green"]);
  });
  it("shows each saved mode and fresh holdout variation", async () => {
    await mount({
      profiles: { red: profile },
      calibration_status: { red: { status: "valid" }, green: { status: "not_calibrated" } },
    });
    expect(wrapper.get('[data-rg="red"]').text()).toContain("Valid");
    expect(wrapper.text()).toContain("20.00% → 1.00%");
    expect(wrapper.get("img").attributes("src")).toContain("/rg_flat_field/preview/red.png");
    expect(
      buttons()
        .find((b) => b.props("action") === "validate")
        .props("submitData").modes,
    ).toEqual(["red"]);
  });
  it("blocks capture on an unavailable controller without requiring motor zero", async () => {
    await mount({ readiness: { ready: false, reason: "Controller is moving" } });
    await wrapper.get('[data-rg="prepared"]').setValue(true);
    expect(calibrate()[0].props("isDisabled")).toBe(true);
    expect(wrapper.text()).toContain("Controller is moving");
  });
  it("invalidates confirmation on edits and requires saving validated parameters", async () => {
    await mount();
    await wrapper.get('[data-rg="prepared"]').setValue(true);
    await wrapper.get('[data-rg-setting="average_frames"]').setValue("8");
    expect(wrapper.vm.prepared).toBe(false);
    expect(calibrate()[0].props("isDisabled")).toBe(true);
    const save = buttons().find((b) => b.props("action") === "set_parameters");
    expect(save.props("isDisabled")).toBe(false);
    await wrapper.get('[data-rg-setting="average_frames"]').setValue("1");
    expect(save.props("isDisabled")).toBe(true);
  });
  it("announces action state to the existing wizard and preserves errors", async () => {
    await mount();
    const emit = vi.spyOn(wrapper.vm.$, "emit");
    wrapper.vm.started("both");
    await flushPromises();
    expect(emit).toHaveBeenCalledTimes(1);
    expect(emit).toHaveBeenCalledWith("actionStarted");
    expect(wrapper.vm.running).toBe(true);
    expect(calibrate()[0].props("isDisabled")).toBe(false);
    expect(calibrate()[1].props("isDisabled")).toBe(true);
    wrapper.vm.onError(new Error("Field is saturated"));
    wrapper.vm.finished("both");
    await flushPromises();
    expect(wrapper.text()).toContain("Field is saturated");
    expect(emit).toHaveBeenCalledTimes(2);
    expect(emit).toHaveBeenLastCalledWith("actionFinished");
    expect(wrapper.vm.prepared).toBe(false);
  });
  it("refreshes actual results after success instead of inventing a valid map", async () => {
    const { state } = await mount();
    state.profiles = { red: profile };
    state.calibration_status.red.status = "valid";
    calibrate()[0].vm.$emit("response", {});
    await flushPromises();
    expect(wrapper.vm.profiles.red.id).toBe("saved-map");
    expect(wrapper.vm.prepared).toBe(false);
  });
  it("does not offer validation for incompatible maps", async () => {
    await mount({
      profiles: { red: profile },
      calibration_status: { red: { status: "incompatible", reason: "Exposure changed" } },
    });
    expect(wrapper.text()).toContain("Exposure changed");
    expect(buttons().find((b) => b.props("action") === "validate")).toBeUndefined();
  });
});

it("distinguishes spatial correction from brightness stability", async () => {
  await mount({
    profiles: {
      red: {
        ...profile,
        validation: {
          ...profile.validation,
          brightness_change_notice: true,
          brightness_drift_fraction: 0.12,
        },
      },
    },
  });
  expect(wrapper.text()).toContain("12.0%");
  expect(wrapper.text()).toContain("do not stabilise the light source");
});

it("shows a failed saved-map check and allows a fresh recheck", async () => {
  await mount({
    profiles: { red: profile },
    calibration_status: { red: { status: "validation_failed", reason: "Spatial pattern changed" } },
  });
  expect(wrapper.get('[data-rg="red"]').text()).toContain("Check failed");
  expect(wrapper.text()).toContain("Spatial pattern changed");
  expect(
    buttons()
      .find((b) => b.props("action") === "validate")
      .props("submitData").modes,
  ).toEqual(["red"]);
});

it("keeps one Cancel usable after reconnecting to an existing calibration", async () => {
  await mount();
  for (const button of calibrate()) button.vm.$emit("update:taskRunning", true);
  await flushPromises();
  expect(calibrate()[0].props("isDisabled")).toBe(false);
  expect(calibrate()[1].props("isDisabled")).toBe(true);
  expect(calibrate()[2].props("isDisabled")).toBe(true);
  expect(wrapper.get('[data-rg="prepared"]').element.disabled).toBe(true);
  for (const button of calibrate()) button.vm.$emit("update:taskRunning", false);
  await flushPromises();
  expect(calibrate()[0].props("isDisabled")).toBe(true);
});

it("clears observed activity when completion unmounts the validation button", async () => {
  await mount({
    profiles: { red: profile },
    calibration_status: { red: { status: "valid" } },
  });
  const button = buttons().find((b) => b.props("action") === "validate");
  button.vm.$emit("update:taskRunning", true);
  await flushPromises();
  expect(wrapper.get('[data-rg="prepared"]').element.disabled).toBe(true);
  button.vm.$emit("response", {
    output: { validation: { red: profile.validation }, white_restored: true },
  });
  button.vm.$emit("finished");
  await flushPromises();
  expect(wrapper.get('[data-rg="prepared"]').element.disabled).toBe(false);
  expect(wrapper.vm.observedKey).toBe(null);
  expect(wrapper.vm.validation.white_restored).toBe(true);
});

it("keeps the validation action and its modal mounted while refreshing results", async () => {
  const { read } = await mount({
    profiles: { red: profile },
    calibration_status: { red: { status: "valid" } },
  });
  const button = buttons().find((b) => b.props("action") === "validate");
  const instance = button.vm;
  let resume;
  const waiting = new Promise((resolve) => {
    resume = resolve;
  });
  const original = read.getMockImplementation();
  read.mockImplementation(async (...args) => {
    await waiting;
    return original(...args);
  });
  const pending = wrapper.vm.load();
  await wrapper.vm.$nextTick();
  expect(buttons().find((b) => b.props("action") === "validate").vm).toBe(instance);
  resume();
  await pending;
});

it("requires explicit JPEG preset without changing saved counts or silently saving a suggested ROI", async () => {
  const legacy = {
    ...parameters,
    measurement_domain: "legacy-raw",
    processing_roi: null,
    minimum_signal_dn: 64,
    maximum_dark_signal_dn: 256,
    average_frames: 32,
    validation_frames: 8,
  };
  const { state, read } = await mount({
    parameters: legacy,
    readiness: { ready: false, reason: "Save JPEG8 preset" },
  });
  expect(wrapper.text()).toContain("Save the explicit JPEG8 preset");
  expect(wrapper.vm.draft.processing_roi).toEqual([102, 0, 810, 760]);
  expect(state.parameters.processing_roi).toBeNull();
  expect(wrapper.emitted("actionStarted")).toBeUndefined();
  expect(read.mock.calls.every(([, , force]) => force === true)).toBe(true);
  const preset = buttons().find((button) => button.props("action") === "use_jpeg_preset");
  expect(preset.props("submitData")).toEqual({ processing_roi: [102, 0, 810, 760] });
  expect(preset.props("isDisabled")).toBe(false);
  expect(
    wrapper.get('[data-rg-setting="average_frames"]').element.closest("fieldset").disabled,
  ).toBe(true);
  await wrapper.get('[data-rg="prepared"]').setValue(true);
  expect(calibrate()[0].props("isDisabled")).toBe(true);
  state.parameters = {
    ...legacy,
    measurement_domain: "processed-jpeg-rgb8",
    processing_roi: [102, 0, 810, 760],
    minimum_signal_dn: 8,
    maximum_dark_signal_dn: 32,
  };
  state.readiness.ready = true;
  preset.vm.$emit("response", { output: state.parameters });
  await flushPromises();
  expect(wrapper.vm.draft.average_frames).toBe(32);
  expect(wrapper.vm.draft.validation_frames).toBe(8);
  expect(wrapper.vm.draft.timeout_s).toBe(180);
  expect(wrapper.vm.draft.maximum_residual_cv).toBe(0.05);
  expect(wrapper.vm.dirty).toBe(false);
});

it("preserves a saved ROI, rejects out-of-bounds or fractional edits, and does not guess unknown dimensions", async () => {
  await mount({ parameters: { ...parameters, processing_roi: [30, 40, 500, 400] } });
  expect(wrapper.vm.draft.processing_roi).toEqual([30, 40, 500, 400]);
  await wrapper.get('[data-rg-roi="0"]').setValue("600");
  expect(wrapper.vm.validRoi).toBe(false);
  expect(
    buttons()
      .find((button) => button.props("action") === "set_parameters")
      .props("isDisabled"),
  ).toBe(true);
  await wrapper.get('[data-rg-roi="0"]').setValue("0.5");
  expect(wrapper.vm.validRoi).toBe(false);
  wrapper.unmount();
  await mount({
    parameters: { ...parameters, measurement_domain: "legacy-raw", processing_roi: null },
    jpeg_measurement_configuration: null,
  });
  expect(wrapper.vm.draft.processing_roi).toEqual(["", "", "", ""]);
  expect(
    buttons()
      .find((button) => button.props("action") === "use_jpeg_preset")
      .props("isDisabled"),
  ).toBe(true);
});

it("does not overwrite ROI or quality edits on polling or refresh", async () => {
  const { state } = await mount();
  await wrapper.get('[data-rg-roi="0"]').setValue("100");
  await wrapper.get('[data-rg-setting="average_frames"]').setValue("12");
  state.parameters = { ...parameters, average_frames: 20 };
  await wrapper.vm.refreshStatus();
  await wrapper.vm.load();
  expect(wrapper.vm.draft.processing_roi[0]).toBe(100);
  expect(wrapper.vm.draft.average_frames).toBe(12);
  expect(wrapper.vm.dirty).toBe(true);
});

it("provides separate enable and confirmed pointer-only reset controls", async () => {
  const { state } = await mount({
    profiles: { red: profile, green: { ...profile, id: "green-map" } },
    calibration_status: {
      red: { status: "disabled", reason: "Correction disabled; saved map retained" },
      green: { status: "valid" },
    },
  });
  const enable = buttons().filter((button) => button.props("action") === "set_enabled");
  expect(enable.map((button) => button.props("submitData"))).toEqual([
    { mode: "red", enabled: true },
    { mode: "green", enabled: false },
  ]);
  const reset = buttons().filter((button) => button.props("action") === "reset_profile");
  expect(reset.map((button) => button.props("submitData"))).toEqual([
    { mode: "red" },
    { mode: "green" },
  ]);
  expect(reset.every((button) => button.props("requiresConfirmation"))).toBe(true);
  expect(reset[0].props("confirmationMessage")).toContain(
    "Historical files and the other colour are kept",
  );
  expect(wrapper.get('[data-rg="red"]').text()).toContain("Disabled — map retained");
  state.profiles = { green: state.profiles.green };
  state.calibration_status.red.status = "not_calibrated";
  reset[0].vm.$emit("response", {});
  await flushPromises();
  expect(wrapper.vm.profiles.green.id).toBe("green-map");
  expect(wrapper.vm.profiles.red).toBeUndefined();
});

it("handles sparse historical profiles without loading a RAW preview or claiming JPEG validity", async () => {
  await mount({
    profiles: { red: { id: "historical-map", method: "native_raw_measured_dark" } },
    calibration_status: { red: { status: "incompatible", reason: "Measurement domain changed" } },
  });
  expect(wrapper.find("img").exists()).toBe(false);
  expect(wrapper.text()).toContain("Historical RAW/incompatible-domain map retained");
  expect(wrapper.text()).toContain("Date unavailable");
  expect(wrapper.text()).not.toContain("Invalid Date");
  expect(buttons().find((button) => button.props("action") === "validate")).toBeUndefined();
});
