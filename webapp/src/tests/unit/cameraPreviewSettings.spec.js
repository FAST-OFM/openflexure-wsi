import { shallowMount, flushPromises } from "@vue/test-utils";
import { createTestingPinia } from "@pinia/testing";
import { afterEach, describe, expect, it, vi } from "vitest";
import CameraPreviewSettings from "../../components/tabContentComponents/settingsComponents/cameraPreviewSettings.vue";
import PropertyControl from "../../components/labThingsComponents/propertyControl.vue";

let wrapper;
afterEach(() => wrapper?.unmount());

describe("preview encoding settings", () => {
  it.each([
    [false, false],
    [false, true],
    [true, false],
    [true, true],
  ])("keeps preview quality editable for lores=%s equalSize=%s", async (lores, equalSize) => {
    const read = vi.fn(async (_thing, property) => {
      if (property === "streaming_mode") return "test";
      return {
        test: {
          use_lores_as_preview: lores,
          main_resolution: equalSize ? [1014, 760] : [2028, 1520],
          lores_resolution: [1014, 760],
        },
      };
    });
    wrapper = shallowMount(CameraPreviewSettings, {
      global: {
        plugins: [
          createTestingPinia({
            createSpy: vi.fn,
            initialState: { settings: { ready: true } },
          }),
        ],
        mocks: { readThingProperty: read },
      },
    });
    await flushPromises();
    const properties = wrapper
      .findAllComponents(PropertyControl)
      .map((control) => control.props("propertyName"));
    expect(properties).toContain("preview_jpeg_quality");
    expect(properties).not.toContain("mjpeg_bitrate");
    expect(wrapper.text()).toContain("Does not change scan images or autofocus");
    expect(wrapper.text()).not.toContain("Hardware MJPEG shared with autofocus");
    expect(wrapper.vm.previewResolution).toEqual(lores || equalSize ? [1014, 760] : [2028, 1520]);
    expect(read).toHaveBeenCalledTimes(2);
  });
});
