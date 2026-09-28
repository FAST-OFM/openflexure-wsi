import { shallowMount, flushPromises } from "@vue/test-utils";
import { createTestingPinia } from "@pinia/testing";
import { describe, it, expect, vi, afterEach, beforeEach } from "vitest";
import CompactHelp from "../../components/genericComponents/compactHelp.vue";
import axios from "axios";
import IlluminationSettings from "../../components/tabContentComponents/settingsComponents/illuminationSettings.vue";
import { useSettingsStore } from "../../stores/settings.js";

vi.mock("axios", () => ({ default: { get: vi.fn(), post: vi.fn() } }));
let wrapper;
let current;
let jpegParameters;
let jpegConfiguration;
const stateUrl = "http://test/api/v3/illumination/state";
const actionUrl = "http://test/api/v3/illumination/set_mode";
const diagnosticActionUrl = "http://test/api/v3/rg_flat_field/select_diagnostic_mode";
const invocationUrl = "http://test/api/v3/action_invocations/light";
const parametersUrl = "http://test/api/v3/rg_flat_field/parameters";
const configurationUrl = "http://test/api/v3/camera/jpeg_measurement_configuration";
const readback = (url) =>
  url === parametersUrl ? jpegParameters : url === configurationUrl ? jpegConfiguration : current;
const light = (mode) => ({
  available: true,
  mode,
  channels: { white: mode === "white", red: mode === "red", green: mode === "green" },
  error: "",
});
const deferred = () => {
  let resolve;
  const promise = new Promise((done) => (resolve = done));
  return { promise, resolve };
};
beforeEach(() => {
  vi.useFakeTimers();
  vi.resetAllMocks();
  current = light("white");
  jpegParameters = {
    measurement_domain: "processed-jpeg-rgb8",
    processing_roi: [102, 0, 810, 760],
  };
  jpegConfiguration = {
    measurement_space: "processed-jpeg-rgb8",
    geometry: { image_size: [1014, 760] },
  };
  axios.get.mockImplementation(async (url) => ({
    data: url === invocationUrl ? { status: "completed" } : readback(url),
  }));
  axios.post.mockResolvedValue({ data: { href: invocationUrl } });
});
afterEach(() => {
  wrapper?.unmount();
  wrapper = null;
  vi.clearAllTimers();
  vi.useRealTimers();
});
async function mount(diagnostics = false) {
  wrapper = shallowMount(IlluminationSettings, {
    global: {
      plugins: [
        createTestingPinia({
          createSpy: vi.fn,
          initialState: { settings: { ready: true, baseUri: "http://test/api/v3" } },
        }),
      ],
      mocks: {
        thingPropertyUrl: (thing, name) => `http://test/api/v3/${thing}/${name}`,
        thingActionAvailable: (thing, action) =>
          diagnostics && thing === "rg_flat_field" && action === "select_diagnostic_mode",
        thingActionUrl: (thing) => (thing === "rg_flat_field" ? diagnosticActionUrl : actionUrl),
      },
    },
  });
  await flushPromises();
}
const button = (channel) => wrapper.get(`[data-channel="${channel}"]`);

describe("controller-backed illumination settings", () => {
  it("only reads on mount and marks the actual active output", async () => {
    await mount();
    expect(axios.post).not.toHaveBeenCalled();
    expect(button("white").attributes("aria-pressed")).toBe("true");
    expect(button("white").text()).toContain("On");
    expect(button("red").text()).toContain("Off");
    expect(axios.get).toHaveBeenCalledWith(stateUrl, { timeout: 5000 });
  });

  it("polls external changes and cleans up its timer", async () => {
    await mount();
    current = light("green");
    await vi.advanceTimersByTimeAsync(2000);
    expect(button("green").attributes("aria-pressed")).toBe("true");
    expect(button("white").attributes("aria-pressed")).toBe("false");
    wrapper.unmount();
    wrapper = null;
    expect(vi.getTimerCount()).toBe(0);
  });

  it.each([
    undefined,
    { available: false, error: "Disconnected" },
    { ...light("red"), mode: null },
  ])("shows unknown, not stale On or guessed Off, when readback is invalid: %j", async (bad) => {
    await mount();
    current = bad;
    await vi.advanceTimersByTimeAsync(2000);
    expect(button("white").text()).toContain("Unknown");
    expect(button("white").attributes("disabled")).toBeDefined();
    expect(axios.post).not.toHaveBeenCalled();
  });

  it("stays busy until completion AND a fresh readback; ignores double click", async () => {
    await mount();
    const complete = deferred();
    const readback = deferred();
    axios.get.mockImplementation((url) =>
      url === invocationUrl ? complete.promise : readback.promise,
    );
    const action = wrapper.vm.selectChannel("red");
    await flushPromises();
    await button("red").trigger("click");
    expect(axios.post).toHaveBeenCalledTimes(1);
    expect(axios.post).toHaveBeenCalledWith(actionUrl, { mode: "red" }, { timeout: 5000 });
    expect(button("red").text()).toContain("Unknown");
    expect(button("red").attributes("disabled")).toBeDefined();
    complete.resolve({ data: { status: "completed" } });
    await flushPromises();
    expect(wrapper.vm.busy).toBe(true);
    readback.resolve({ data: light("red") });
    await action;
    expect(wrapper.vm.busy).toBe(false);
    expect(button("red").attributes("aria-pressed")).toBe("true");
  });

  it("captures a corrected RED snapshot through the diagnostic action and refreshes it", async () => {
    const output = {
      id: "frame-id",
      mode: "red",
      flat_field_applied: true,
      profile_id: "12345678-aaaa-bbbb-cccc-123456789000",
      image_href: "/rg_flat_field/diagnostic/frame-id/red.png",
      sensor_timestamp_ns: 99,
    };
    axios.post.mockImplementation(async (_url, body) => {
      current = light(body.mode);
      return { data: { href: invocationUrl } };
    });
    axios.get.mockImplementation(async (url) => ({
      data: url === invocationUrl ? { status: "completed", output } : readback(url),
    }));
    await mount(true);
    await button("red").trigger("click");
    await flushPromises();
    expect(axios.post).toHaveBeenCalledWith(
      diagnosticActionUrl,
      { mode: "red" },
      { timeout: 5000 },
    );
    expect(wrapper.get(".diagnostic-image").attributes("src")).toBe(
      "http://test/api/v3/rg_flat_field/diagnostic/frame-id/red.png?frame=99",
    );
    expect(wrapper.text()).toContain("flat-field applied · profile 12345678");
    const roiHelp = wrapper
      .findAllComponents(CompactHelp)
      .find((help) => help.props("label") === "R/G preview area");
    expect(roiHelp.props("text")).toContain("Processed JPEG8");
    expect(roiHelp.props("text")).toContain("102, 0, 810, 760");
    await wrapper.get("[data-diagnostic-refresh]").trigger("click");
    await flushPromises();
    expect(axios.post).toHaveBeenLastCalledWith(
      diagnosticActionUrl,
      { mode: "red" },
      { timeout: 5000 },
    );
  });

  it.each(["legacy", "invalid-roi", "missing-geometry"])(
    "keeps WHITE/OFF available but blocks R/G capture when %s",
    async (reason) => {
      if (reason === "legacy") jpegParameters = { measurement_domain: "legacy-raw" };
      else if (reason === "invalid-roi") jpegParameters.processing_roi = [1000, 0, 810, 760];
      else jpegConfiguration = null;
      await mount(true);
      expect(axios.post).not.toHaveBeenCalled();
      expect(button("red").element.disabled).toBe(true);
      expect(button("green").element.disabled).toBe(true);
      expect(button("white").element.disabled).toBe(false);
      expect(wrapper.text()).toContain("WHITE and OFF remain available");
      await wrapper.vm.applyMode("red");
      expect(axios.post).not.toHaveBeenCalled();
      await wrapper.vm.selectChannel("white");
      expect(axios.post).toHaveBeenLastCalledWith(
        diagnosticActionUrl,
        { mode: "off" },
        { timeout: 5000 },
      );
      current = light("red");
      await wrapper.vm.refreshState();
      expect(button("red").element.disabled).toBe(false);
      await wrapper.vm.selectChannel("red");
      expect(axios.post).toHaveBeenLastCalledWith(
        diagnosticActionUrl,
        { mode: "off" },
        { timeout: 5000 },
      );
      await wrapper.vm.selectChannel("white");
      expect(axios.post).toHaveBeenLastCalledWith(
        diagnosticActionUrl,
        { mode: "white" },
        { timeout: 5000 },
      );
    },
  );

  it("shows an uncorrected JPEG diagnostic reason and tolerates absent profile metadata", async () => {
    const output = {
      mode: "green",
      image_href: "/rg_flat_field/diagnostic/green.png",
      flat_field_applied: false,
      correction_reason: "GREEN flat-field is disabled",
    };
    axios.post.mockImplementation(async (_url, body) => {
      current = light(body.mode);
      return { data: { href: invocationUrl } };
    });
    axios.get.mockImplementation(async (url) => ({
      data: url === invocationUrl ? { status: "completed", output } : readback(url),
    }));
    await mount(true);
    await wrapper.vm.selectChannel("green");
    expect(wrapper.text()).toContain(
      "Uncorrected JPEG8 GREEN frame — GREEN flat-field is disabled",
    );
    output.flat_field_applied = true;
    await wrapper.vm.captureCurrent();
    expect(wrapper.text()).toContain("profile unavailable");
  });

  it("clicking the active channel requests OFF", async () => {
    await mount();
    current = light("off");
    await button("white").trigger("click");
    await flushPromises();
    expect(axios.post).toHaveBeenCalledWith(actionUrl, { mode: "off" }, { timeout: 5000 });
    expect(button("white").text()).toContain("Off");
  });

  it("polls pending actions without resending the command", async () => {
    await mount();
    axios.get
      .mockResolvedValueOnce({ data: { status: "running" } })
      .mockResolvedValueOnce({ data: { status: "completed" } });
    current = light("red");
    const action = wrapper.vm.selectChannel("red");
    await flushPromises();
    expect(wrapper.vm.busy).toBe(true);
    await vi.advanceTimersByTimeAsync(200);
    await action;
    expect(axios.post).toHaveBeenCalledTimes(1);
    expect(button("red").text()).toContain("On");
  });

  it("reports action failure and re-reads actual outputs without retry", async () => {
    await mount();
    axios.get.mockResolvedValueOnce({
      data: { status: "error", log: [{ message: "Scan lock busy" }] },
    });
    await button("red").trigger("click");
    await flushPromises();
    expect(wrapper.get('[role="alert"]').text()).toContain("Scan lock busy");
    expect(button("white").text()).toContain("On");
    expect(button("red").text()).toContain("Off");
    expect(axios.post).toHaveBeenCalledTimes(1);
  });

  it("a lost POST acknowledgement is not retried and status is still checked", async () => {
    await mount();
    axios.post.mockRejectedValueOnce(new Error("Timeout"));
    current = light("off");
    await button("red").trigger("click");
    await flushPromises();
    expect(wrapper.get('[role="alert"]').text()).toContain("Timeout");
    expect(button("red").text()).toContain("Off");
    expect(axios.post).toHaveBeenCalledTimes(1);
  });

  it("ignores a stale pre-action state response", async () => {
    await mount();
    const old = deferred();
    axios.get.mockImplementationOnce(() => old.promise);
    const refresh = wrapper.vm.refreshState();
    current = light("red");
    await wrapper.vm.selectChannel("red");
    old.resolve({ data: light("white") });
    await refresh;
    expect(button("red").attributes("aria-pressed")).toBe("true");
  });

  it("disconnect invalidates readback and prevents new light commands", async () => {
    await mount();
    const old = deferred();
    axios.get.mockImplementationOnce(() => old.promise);
    const refresh = wrapper.vm.refreshState();
    useSettingsStore().ready = false;
    await flushPromises();
    old.resolve({ data: light("white") });
    await refresh;
    expect(button("white").text()).toContain("Unknown");
    await wrapper.vm.selectChannel("red");
    expect(axios.post).not.toHaveBeenCalled();
  });

  it("does not continue action polling after unmount", async () => {
    await mount();
    const pending = deferred();
    axios.get.mockImplementationOnce(() => pending.promise);
    const action = wrapper.vm.selectChannel("red");
    await flushPromises();
    wrapper.unmount();
    wrapper = null;
    pending.resolve({ data: { status: "running" } });
    await vi.advanceTimersByTimeAsync(200);
    await action;
    expect(axios.get).toHaveBeenCalledTimes(2);
    expect(vi.getTimerCount()).toBe(0);
  });
});
