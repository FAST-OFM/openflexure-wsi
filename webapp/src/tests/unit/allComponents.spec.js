import { describe, it, expect, beforeEach, afterEach, afterAll, vi } from "vitest";
import { shallowMount, flushPromises } from "@vue/test-utils";
import { createTestingPinia } from "@pinia/testing";
import { componentOverrides } from "./overrides";

/**
 * @file Automated component smoke testing suite.
 * @description Iterates through all project components and performs a
 * shallow mount on each. This acts as a first line of defense to catch
 * fatal DOM runtime errors, missing dependencies, memory leaks, lifecycle or script crashes
 * before they reach production.
 * @note CI Execution:
 * When running this script in a CI/CD pipeline, it must be executed with strict memory
 * safety and process isolation to handle the heavy footprint of mounting the whole set of Vue components ().
 * The `test:unit` script implements the following safety nets:
 * - `NODE_OPTIONS=--expose-gc` & `--logHeapUsage`: Unlocks the V8 garbage collector to aggressively clear RAM.
 * - `--pool=forks` & `--isolate=true`: Boots a dedicated Node.js child process per file to prevent cross-DOM pollution.
 * - `--detectAsyncLeaks`: Actively catches hanging `setIntervals` or unresolved API promises.
 * - `--no-cache`: Bypasses Vite caching to guarantee a pristine execution environment.
 * - `--reporter=verbose`: Forces cloud logs to print exact component names for easier debugging.
 * @requires overrides.js for component-specific prop and mock configurations.
 */

/**
 * Vite eagerly grabs all .vue components, explicitly excluding the experimental folder.
 * @type {Record<string, { default: any }>} A dictionary mapping file paths to their Vue component modules.
 */
const componentModules = import.meta.glob(
  ["../../components/**/*.vue", "!../../components/experimental/**/*.vue"],
  { eager: true },
);

/**
 * @note IMPORTANT: Skip list, add here components that have their own test .spec.js file
 * */
const skipList = ["loggingContent.vue", "logCard.vue"];

/**
 * Test description: this is a fast unit test
 * The objective of this testing is find early
 * component loading issues like bad component setups
 */
describe("Unit Test", () => {
  let wrapper;

  /**
   * Environment setup
   * set Fake timers and Mocks
   */
  beforeEach(() => {
    vi.useFakeTimers();
    vi.resetAllMocks();
  });

  /**
   * Clean up after every single test
   * Unmounts any present component wrapper=
   * Clean up virtual DOM and memory bindings
   * Forces V8 engine to immediately run Garbage Collection
   * clears out timers to avoid leaks
   */
  afterEach(() => {
    if (wrapper) {
      wrapper.unmount();
      wrapper = null;
    }

    document.body.innerHTML = "";

    if (global.gc) {
      global.gc();
    }

    vi.clearAllTimers();
    vi.useRealTimers();
  });

  afterAll(async () => {
    await new Promise((resolve) => setTimeout(resolve, 15));
  });

  /**
   * Loop through the file paths
   * Unwraps the component and exposes its definition using ".default"
   * Includes meta information like name, props, components, methods.
   * Do not confuse with export default from script component section
   * Skips components at the skipList from files that are explicitly skipped
   * Also skips components which name starts with the word "experimental"
   * allowing to work with edge case components if needed
   * is discouraged to push components that won't pass a smoke test
   * */
  for (const path in componentModules) {
    const component = componentModules[path].default;
    const fileName = path.split("/").pop();
    const isSkipped = skipList.includes(fileName) || path.toLowerCase().includes("experimental");

    /**
     * This check skips vue files that are corrupted or have no meta content
     * return is preferred as a wrong path in file could lead to halt on CI pipeline or over verbose output
     * An error here means a wrong path or file access error
     * This was needed during the writing of this script
     * Leaving for other future edge cases usage
     * This fails the overall test
     * */
    if (!component || undefined) {
      console.error(component);

      const errorName = fileName + "Wrong Path";

      it(`${errorName} has no correct Path`, () => {
        expect.fail(`Incorrect Path parsing for "${fileName}"`);
      });

      return;
    }

    /**
     * Dynamically assign the test runner (it.skip if in the list, otherwise standard it)
     * Run the test
     * Look up the specific config for this file, default to an empty object
     * Override specific props
     * Inject the specific state if it exists, otherwise use empty object
     * * Note on Global Mocks:
     * The mock functions below stub out standard Mixin methods that typically interact with the Pinia store.
     * These should not be moved to `overrides.js` Defining them here significantly reduces boilerplate configuration inside `overrides.js`.
     */
    const testRunner = isSkipped ? it.skip : it;

    testRunner(`Shallow mounts ${fileName} without crashing`, async () => {
      const override = componentOverrides[fileName] || {};

      wrapper = shallowMount(component, {
        attachTo: document.body,

        props: override.props || {},

        global: {
          plugins: [
            createTestingPinia({
              createSpy: vi.fn,
              initialState: override.initialState || {},
            }),
          ],
          mocks: {
            readThingProperty: () => ({}),
            thingActionAvailable: () => true,
            thingAvailable: () => ({}),
            thingDescription: () => ({
              actions: {},
              properties: {},
            }),
            thingDescriptions: () => ({
              test_thing: {
                properties: {
                  test_property_1: {},
                  test_property_2: {},
                },
              },
            }),
            getOngoingAction: () => Promise.resolve(),
            getThingEndpoint: () => Promise.resolve([]),
            modalError: "",
            ...(override.global?.mocks || {}),
            ...(override.mocks || {}),
          },
        },
      });

      await flushPromises();
      vi.advanceTimersByTime(200);
      expect(wrapper.exists()).toBe(true);
    });
  }
});
