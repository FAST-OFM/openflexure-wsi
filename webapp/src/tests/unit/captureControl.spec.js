import { shallowMount } from "@vue/test-utils";
import { expect, it, vi } from "vitest";
import CaptureControl from "../../components/tabContentComponents/controlComponents/captureControl.vue";
import ActionButton from "../../components/labThingsComponents/actionButton.vue";

it("keeps capture compact and accessible without changing the destination payload", async () => {
  const wrapper = shallowMount(CaptureControl, { global: { mocks: { modalError: vi.fn() } } });
  try {
    expect(wrapper.find(".menu-subheading").exists()).toBe(false);
    expect(wrapper.text()).not.toContain("On capture:");
    const destination = wrapper.get("#saveLocation");
    expect(destination.attributes("aria-label")).toBe("Capture destination");
    const button = wrapper.findComponent(ActionButton);
    expect(button.props("submitLabel")).toBe("Capture");
    expect(button.props("submitData")).toEqual({
      capture_mode: "standard",
      retain_image: true,
    });
    await destination.setValue("download");
    expect(button.props("submitData").retain_image).toBe(false);
    expect(button.props("submitOnEvent")).toBe("globalCaptureEvent");
  } finally {
    wrapper.unmount();
  }
});
