import { mount, flushPromises } from "@vue/test-utils";
import { describe, it, expect, vi, afterEach } from "vitest";
import MeasurementPreviewStatus from "../../components/genericComponents/measurementPreviewStatus.vue";

let wrapper;
afterEach(() => {
  wrapper?.unmount();
  vi.useRealTimers();
});
function create(read, enabled = true) {
  wrapper = mount(MeasurementPreviewStatus, {
    props: { enabled },
    global: {
      mocks: {
        thingDescription: () => ({ properties: { measurement_preview_status: {} } }),
        readThingProperty: read,
      },
    },
  });
}
describe("Measurement preview hold status", () => {
  it("shows hold/wait/live without changing or synthesizing images", async () => {
    vi.useFakeTimers();
    const read = vi.fn().mockResolvedValue({ active: true, holding: true });
    create(read);
    await flushPromises();
    expect(wrapper.text()).toContain("waiting for next WHITE");
    expect(wrapper.find("img").exists()).toBe(false);
    read.mockResolvedValue({ active: false, holding: true });
    await vi.advanceTimersByTimeAsync(1000);
    expect(wrapper.text()).toContain("Waiting for fresh WHITE");
    read.mockResolvedValue({ active: false, holding: false });
    await vi.advanceTimersByTimeAsync(1000);
    expect(wrapper.text()).toBe("");
  });
  it("does not poll hidden streams and clears its timer on unmount", async () => {
    vi.useFakeTimers();
    const read = vi.fn().mockResolvedValue({ holding: false });
    create(read, false);
    await vi.advanceTimersByTimeAsync(3000);
    expect(read).not.toHaveBeenCalled();
    await wrapper.setProps({ enabled: true });
    await flushPromises();
    expect(read).toHaveBeenCalledTimes(1);
    wrapper.unmount();
    await vi.advanceTimersByTimeAsync(3000);
    expect(read).toHaveBeenCalledTimes(1);
  });
});
