import { afterEach, describe, expect, it, vi } from "vitest";
import SlideScan from "@/components/tabContentComponents/slideScanContent.vue";

function scanVm(read) {
  const vm = {
    ...SlideScan.data.call({ $options: { name: "SlideScanContent" } }),
    baseUri: "http://TEST_ONLY.invalid",
    taskId: "TEST_ONLY_action",
    taskUrl: "/TEST_ONLY_action",
    readThingProperty: read,
  };
  Object.defineProperty(vm, "scanning", { get: () => vm.taskId && vm.taskUrl });
  for (const [name, method] of Object.entries(SlideScan.methods)) vm[name] = method.bind(vm);
  return vm;
}

function details(outcome) {
  return {
    name: "TEST_ONLY_scan",
    stitch_timestamp: null,
    image_count: 1,
    scan_phase: "Complete",
    focus: { outcome },
    settings: { frozen: "TEST_ONLY_snapshot" },
  };
}

afterEach(() => {
  vi.clearAllTimers();
  vi.useRealTimers();
});

describe("terminal focus polling", () => {
  it("reads final state once after completion and does not schedule more polls", async () => {
    vi.useFakeTimers();
    const terminal = details("captured");
    const read = vi.fn().mockResolvedValue(terminal);
    const vm = scanVm(read);
    vm.scanDetails = details("focused");
    await vm.onScanCompleted();
    expect(vm.scanComplete).toBe(true);
    expect(vm.scanDetails).toEqual(terminal);
    expect(read).toHaveBeenCalledOnce();
    expect(read).toHaveBeenCalledWith("smart_scan", "latest_scan_live_details", true);
    await vi.advanceTimersByTimeAsync(5000);
    expect(read).toHaveBeenCalledOnce();
  });

  it("ignores an older in-flight poll arriving after the terminal read", async () => {
    vi.useFakeTimers();
    let release;
    const oldPoll = new Promise((resolve) => {
      release = resolve;
    });
    const terminal = details("cancelled");
    const read = vi.fn().mockReturnValueOnce(oldPoll).mockResolvedValue(terminal);
    const vm = scanVm(read);
    const pending = vm.pollScan();
    await vm.onScanCompleted();
    release(details("focused"));
    await pending;
    expect(vm.scanDetails).toEqual(terminal);
    expect(vi.getTimerCount()).toBe(0);
  });

  it("does not repopulate a closed task from a late read", async () => {
    vi.useFakeTimers();
    let release;
    const read = vi.fn(
      () =>
        new Promise((resolve) => {
          release = resolve;
        }),
    );
    const vm = scanVm(read);
    const pending = vm.pollScan();
    vm.closeTask();
    release(details("captured"));
    await pending;
    expect(vm.scanDetails).toBeNull();
    expect(vm.taskId).toBeNull();
    expect(vi.getTimerCount()).toBe(0);
  });

  it("marks the final status unavailable instead of claiming the prior state is final", async () => {
    vi.useFakeTimers();
    const vm = scanVm(vi.fn().mockResolvedValue(null));
    vm.scanDetails = details("focused");
    await vm.onScanCompleted();
    expect(vm.scanStatusError).toBe("Final focus status could not be confirmed.");
    expect(vi.getTimerCount()).toBe(0);
  });
});
