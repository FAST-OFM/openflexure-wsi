import { describe, expect, it } from "vitest";
import { mount } from "@vue/test-utils";
import PortraitWarning from "@/components/genericComponents/portraitWarning.vue";

describe("PortraitWarning", () => {
  it("renders the initial portrait warning", () => {
    const wrapper = mount(PortraitWarning);

    expect(wrapper.text()).toContain("Please rotate your device");
    expect(wrapper.text()).toContain("This app is designed to be used in landscape orientation.");
  });

  it("does not show the confirmation initially", () => {
    const wrapper = mount(PortraitWarning);

    expect(wrapper.text()).not.toContain("Continue in portrait mode?");
    expect(wrapper.text()).not.toContain("Continue");
  });

  it("shows confirmation when close is clicked", async () => {
    const wrapper = mount(PortraitWarning);

    await wrapper.get(".portrait-warning-close").trigger("click");

    expect(wrapper.text()).toContain("Continue in portrait mode?");
    expect(wrapper.text()).toContain("Continue");
    expect(wrapper.text()).toContain("Cancel");
  });

  it("returns to the warning when Cancel is clicked", async () => {
    const wrapper = mount(PortraitWarning);

    await wrapper.get(".portrait-warning-close").trigger("click");
    await wrapper.get(".uk-button-default").trigger("click");

    expect(wrapper.text()).toContain("Please rotate your device");
    expect(wrapper.text()).not.toContain("Continue in portrait mode?");
  });

  it("dismisses the warning when Continue is clicked", async () => {
    const wrapper = mount(PortraitWarning);

    await wrapper.get(".portrait-warning-close").trigger("click");
    await wrapper.get(".uk-button-primary").trigger("click");

    expect(wrapper.find(".portrait-warning").exists()).toBe(false);
  });
});
