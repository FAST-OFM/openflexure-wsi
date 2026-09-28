/**
 * @module setup
 */

import { vi, beforeEach, afterEach } from "vitest";

// Happy DOM looks enough like a browser for Vue to wait for a late devtools
// injection. Supplying the inert hook used by headless test environments keeps
// Vue from creating its three-second replay timer in every mounted-component
// test, while leaving application timers visible to async-leak detection.
vi.stubGlobal("__VUE_DEVTOOLS_GLOBAL_HOOK__", { emit() {} });

/**
 * @file: Global setup for unit tests.
 * @description This file sets up the testing environment for all unit tests, including:
 * - Mocking localStorage to prevent side effects and ensure test isolation.
 * - Spying on console.warn and console.error to catch any Vue warnings or uncaught exceptions during component mounting.
 * - Cleaning up the DOM and memory after each test to prevent leaks and ensure a fresh state for subsequent tests.
 * - Enforcing strict failure on any Vue warnings or console errors to maintain high code quality and catch issues early.
 */

let consoleWarnWatchdog;
let consoleErrorWatchdog;

/**
 * Mock implementation of localStorage for testing purposes.
 * @private
 */
const localStorageMock = (() => {
  let store = {};
  return {
    getItem: (key) => store[key] || null,
    setItem: (key, value) => {
      store[key] = value.toString();
    },
    removeItem: (key) => {
      delete store[key];
    },
    clear: () => {
      store = {};
    },
  };
})();

/**
 * Override Node's native localStorage
 */
vi.stubGlobal("localStorage", localStorageMock);

/**
 * clean initial state before each test and set up console spies to catch warnings and errors.
 */
beforeEach(() => {
  localStorage.clear();
  consoleWarnWatchdog = vi.spyOn(console, "warn");
  consoleErrorWatchdog = vi.spyOn(console, "error");
});

/**
 * After each test, check for any Vue warnings or console errors that occurred during component mounting.
 * If any warnings or errors are detected, fail the test and print the relevant messages for debugging.
 */
afterEach(({ task }) => {
  if (!consoleWarnWatchdog) return;

  /** Grab all warnings */
  const warnings = consoleWarnWatchdog.mock.calls;

  /** Clean up the spy so it doesn't leak */
  consoleWarnWatchdog.mockRestore();

  /** Filter specifically for Vue warnings */
  const vueWarnings = warnings.filter(
    (args) => typeof args[0] === "string" && args[0].includes("[Vue warn]"),
  );

  /** If Vue has warns forcefully fail this test block --max-warnings=0 */
  if (vueWarnings.length > 0) {
    const warningMessages = vueWarnings.map((args) => args.join(" ")).join("\n\n");

    /** Throw a plain string instead of using expect.fail() or new Error()!
     * Without an Error object, there is no stack trace, so Vitest CANNOT show a code snippet.
     */
    throw `[Vue warn] Failure in "${task.name}":\n\n${warningMessages}`;
  }
  /** Add watchdog for console.error to catch any uncaught exceptions during component mounting */
  if (consoleErrorWatchdog) {
    const errors = consoleErrorWatchdog.mock.calls;
    consoleErrorWatchdog.mockRestore();

    if (errors.length > 0) {
      const errorMessages = errors.map((args) => args.join(" ")).join("\n\n");
      throw `[Console Error] Hard crash in "${task.name}":\n\n${errorMessages}`;
    }
  }
});
