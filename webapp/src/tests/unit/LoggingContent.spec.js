import { shallowMount, flushPromises } from "@vue/test-utils";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { createTestingPinia } from "@pinia/testing";
import { setActivePinia } from "pinia";
import axios from "axios";
import realLogs from "../fixtures/realLog.json";
import LoggingContent from "../../components/tabContentComponents/loggingContent.vue";

// Mock Axios
vi.mock("axios", () => {
  return {
    default: {
      get: vi.fn(),
      post: vi.fn(),
      put: vi.fn(),
      delete: vi.fn(),
      patch: vi.fn(),
    },
  };
});

// Mock VueUse to prevent IntersectionObserver crashes in JSDOM
// This will be removed once vue-route is implemented
vi.mock("@vueuse/core", () => ({
  useIntersectionObserver: vi.fn(() => ({
    stop: vi.fn(),
  })),
}));

// Test Description
describe("Test LoggingContent.vue", () => {
  let wrapper;

  // Define path to a real log file and read it
  const mockLogData = realLogs;

  // Things to do before each test
  beforeEach(async () => {
    // Reset mocks before each test
    vi.resetAllMocks();

    // Set axios to return our fake logs
    axios.get.mockResolvedValue({ data: mockLogData });

    // Mount the component with a fake Pinia store
    wrapper = shallowMount(LoggingContent, {
      attachTo: document.body,
      global: {
        plugins: [
          createTestingPinia({
            createSpy: vi.fn,
            initialState: {
              settings: { baseUri: "http://microscope.local:5000/api/v3" },
            },
          }),
        ],
        // Simplify child components
        stubs: {
          PaginateLinks: true,
          EndpointButton: true,
          transition: true,
          teleport: true,
          settings: true,
        },
      },
    });
    // flush so we avoid leaving uncompleted processes running on background
    await flushPromises();
  });

  // Tear down wrapper, unmount testing component
  afterEach(() => {
    if (wrapper) {
      wrapper.unmount(); // Clean up virtual DOM and memory bindings
    }
    setActivePinia(undefined);
    document.body.innerHTML = "";

    if (global.gc) {
      global.gc(); // Forces V8 engine to immediately run Garbage Collection
    }
    vi.clearAllTimers();
    vi.useRealTimers();
  });

  // Test 1: Render check, if things do exist in the page as intended
  it("renders the component correctly", async () => {
    // Check if component is been loaded and it is rendered
    expect(wrapper.exists()).toBe(true);
    // Check if navbar exists
    expect(wrapper.find(".logging-navbar").exists()).toBe(true);
  });

  // Test 2: Check log parsing to see if specific INFO exists
  it("fetches logs and parses them correctly when updateLogs is called", async () => {
    // Trigger the method
    await wrapper.vm.updateLogs();
    await flushPromises();

    // Verify Axios was called with the correct URI from the Pinia store
    expect(axios.get).toHaveBeenCalledWith("http://microscope.local:5000/api/v3/log/");

    // Verify the logs have correct file length
    // Helps validate formatting, etc
    expect(wrapper.vm.logs.length).toBe(88);

    // Check the most recent log
    expect(wrapper.vm.logs[0].level).toBe("ERROR");
    expect(wrapper.vm.logs[0].logger).toContain("camera_stage_mapping");
  });

  // Test 3: Filter logging file information
  it("shows info and above groups by default", async () => {
    await wrapper.vm.updateLogs();
    expect(wrapper.vm.filterLevel).toBe("INFO");
    expect(wrapper.vm.filteredGroups).toHaveLength(4);
  });
  it("shows WARNING and above groups when WARNING is selected", async () => {
    await wrapper.vm.updateLogs();

    wrapper.vm.filterLevel = "WARNING";
    await wrapper.vm.$nextTick();

    expect(wrapper.vm.filterLevel).toBe("WARNING");
    expect(wrapper.vm.filteredGroups).toHaveLength(1);
  });

  it("shows all groups when DEBUG is selected", async () => {
    await wrapper.vm.updateLogs();

    wrapper.vm.filterLevel = "DEBUG";
    await wrapper.vm.$nextTick();

    expect(wrapper.vm.filteredGroups).toHaveLength(4);
  });

  it("only shows errors when ERROR is selected", async () => {
    await wrapper.vm.updateLogs();

    wrapper.vm.filterLevel = "ERROR";
    await wrapper.vm.$nextTick();

    expect(wrapper.vm.filteredGroups).toHaveLength(1);
  });

  it("shows no groups when CRITICAL is selected", async () => {
    await wrapper.vm.updateLogs();

    wrapper.vm.filterLevel = "CRITICAL";
    await wrapper.vm.$nextTick();

    expect(wrapper.vm.filteredGroups).toHaveLength(0);
  });

  it("keeps the failed calibration group visible at ERROR level", async () => {
    await wrapper.vm.updateLogs();

    wrapper.vm.filterLevel = "ERROR";
    await wrapper.vm.$nextTick();

    expect(wrapper.vm.filteredGroups[0].highestLevel).toBe("ERROR");
    expect(wrapper.vm.filteredGroups[0].context).toBe("calibrate_xy");
  });

  it("filters out INFO-only smart scan groups at WARNING level", async () => {
    await wrapper.vm.updateLogs();

    const warningGroups = wrapper.vm.filteredGroups;

    expect(warningGroups.some((group) => group.level === "WARNING")).toBe(false);
  });

  // Test 4: Click buttons
  // This required the component file to be modified by adding data-test-id flags.
  // Flags are needed as classes and text content may change by translation or style change.
  it("click buttons", async () => {
    // Click download button
    const downloadButton = wrapper.find('[data-test-id="download-btn"]');
    expect(downloadButton.exists()).toBe(true);
    await downloadButton.trigger("click");

    // Click update button
    const updateButton = wrapper.find('[data-test-id="update-btn"]');
    expect(updateButton.exists()).toBe(true);
    await updateButton.trigger("click");
  });

  // Prevents Vitest "Async Leak" errors by giving Vue's internal
  // DevTools timer a few milliseconds to finish before teardown.
  afterAll(async () => {
    await new Promise((resolve) => setTimeout(resolve, 15));
  });
});
