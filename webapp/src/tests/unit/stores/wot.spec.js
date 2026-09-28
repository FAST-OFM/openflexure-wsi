import { setActivePinia, createPinia } from "pinia";
import { describe, it, expect, beforeEach, vi } from "vitest";
import axios from "axios";
import { useWotStore, findFormHref } from "../../../stores/wot";

// Mock Axios
vi.mock("axios");

describe("WoT Store", () => {
  // A reusable dummy Thing Description (TD)
  const mockTD = {
    base: "http://microscope.local/api/v3/",
    properties: {
      status: {
        forms: [{ op: ["readproperty", "writeproperty"], href: "status" }],
      },
    },
    actions: {
      toggle: {
        forms: [{ op: "invokeaction", href: "http://external.com/api/v3/toggle" }],
      },
    },
  };

  beforeEach(() => {
    // New Pinia for every test to avoid leaks and inter-playing states
    // Clear axios call history between tests
    setActivePinia(createPinia());
    vi.clearAllMocks();
  });

  describe("Standalone Helper: findFormHref", () => {
    it("returns undefined if affordance is missing", () => {
      expect(findFormHref(undefined, "read")).toBeUndefined();
    });

    it("finds href when op is an array", () => {
      const affordance = mockTD.properties.status;
      expect(findFormHref(affordance, "readproperty")).toBe("status");
    });

    it("finds href when op is a string", () => {
      const affordance = mockTD.actions.toggle;
      expect(findFormHref(affordance, "invokeaction")).toBe("http://external.com/api/v3/toggle");
    });

    it("returns undefined if op does not match", () => {
      const affordance = mockTD.properties.status;
      expect(findFormHref(affordance, "fakeoperation")).toBeUndefined();
    });
  });

  describe("State & Basic Actions", () => {
    it("adds, removes, and clears thing descriptions", () => {
      const store = useWotStore();

      store.addThingDescription("camera", mockTD);
      expect(store.thingDescriptions["camera"]).toEqual(mockTD);
      expect(store.thingList).toContain("camera");

      store.removeThingDescription("camera");
      expect(store.thingDescriptions["camera"]).toBeUndefined();
      expect(store.thingList.length).toBe(0);

      // Test clear all
      store.addThingDescription("cam1", mockTD);
      store.addThingDescription("cam2", mockTD);
      store.removeAllThingDescriptions();
      expect(store.thingList.length).toBe(0);
    });

    it("correctly identifies if a thing is available", () => {
      const store = useWotStore();
      store.addThingDescription("camera", mockTD);

      expect(store.thingAvailable("camera")).toBe(true);
      expect(store.thingAvailable("stage")).toBe(false);
    });
  });

  describe("Network Actions (Axios)", () => {
    it("fetches a single Thing Description WITH a provided name", async () => {
      const store = useWotStore();
      axios.get.mockResolvedValueOnce({ data: mockTD });

      await store.fetchThingDescription("http://microscope.local/api/v3/camera", "myCamera");

      expect(axios.get).toHaveBeenCalledWith("http://microscope.local/api/v3/camera");
      expect(store.thingDescriptions["myCamera"]).toEqual(mockTD);
    });

    it("fetches a single Thing Description and extracts name from URI if missing", async () => {
      const store = useWotStore();
      axios.get.mockResolvedValueOnce({ data: mockTD });

      // The logic should split by "/" and grab "stage"
      await store.fetchThingDescription("http://microscope.local/api/v3/stage/");

      expect(store.thingDescriptions["stage"]).toEqual(mockTD);
    });

    it("fetches multiple Thing Descriptions", async () => {
      const store = useWotStore();
      axios.get.mockResolvedValueOnce({
        status: 200,
        data: {
          "/sensor/": { title: "Sensor TD" },
          motor: { title: "Motor TD" },
        },
      });

      await store.fetchThingDescriptions("http://microscope.local/api/v3/things");

      // The logic strips slashes: "/sensor/" -> "sensor"
      expect(store.thingDescriptions["sensor"].title).toBe("Sensor TD");
      expect(store.thingDescriptions["motor"].title).toBe("Motor TD");
    });

    it("throws an error if fetchThingDescriptions fails", async () => {
      const store = useWotStore();
      axios.get.mockResolvedValueOnce({ status: 404 });

      await expect(store.fetchThingDescriptions("http://bad-url")).rejects.toMatch(
        "Could not retrieve",
      );
    });
  });

  describe("TD Parsing & URL Generation", () => {
    beforeEach(() => {
      const store = useWotStore();
      const mockTD = {
        base: "http://microscope.local/api/v3/",
        properties: {
          status: {
            forms: [{ op: ["readproperty", "writeproperty"], href: "status" }],
          },
        },
        actions: {
          toggle: {
            forms: [{ op: "invokeaction", href: "http://external.com/api/v3/toggle" }],
          },
        },
      };
      store.addThingDescription("microscope", mockTD);
    });

    it("checks affordance availability (and covers Line 55)", () => {
      const store = useWotStore();

      // Success case
      expect(store.thingAffordanceAvailable("microscope", "properties", "status")).toBe(true);

      // Missing Thing (!td)
      expect(store.thingAffordanceAvailable("fakeThing", "properties", "status")).toBe(false);

      // Missing Property inside an existing Category
      expect(store.thingAffordanceAvailable("microscope", "properties", "fakeProp")).toBe(false);

      // Thing exists, but the Category/AffordanceType ('events') does not exist at all!
      expect(store.thingAffordanceAvailable("microscope", "events", "someEvent")).toBe(false);
    });

    it("handles completely missing forms, affordances, and operations", () => {
      const store = useWotStore();

      // Missing Thing entirely
      expect(
        store.thingFormUrl("fakeThing", "properties", "missing", "read", true),
      ).toBeUndefined();
      expect(() => {
        store.thingFormUrl("fakeThing", "properties", "missing", "read", false);
      }).toThrowError(/Could not find form/);

      // The 'microscope' exists, but 'fakeProp' does not.
      expect(
        store.thingFormUrl("microscope", "properties", "fakeProp", "read", true),
      ).toBeUndefined();
      expect(() => {
        store.thingFormUrl("microscope", "properties", "fakeProp", "read", false);
      }).toThrowError(/Could not find form/);

      // 'microscope' and 'status' exist, but 'fakeOp' is missing.
      expect(
        store.thingFormUrl("microscope", "properties", "status", "fakeOp", true),
      ).toBeUndefined();
      expect(() => {
        store.thingFormUrl("microscope", "properties", "status", "fakeOp", false);
      }).toThrowError(/Could not find form/);
    });

    it("thingFormUrl concats base url with relative href", () => {
      const store = useWotStore();
      const url = store.thingFormUrl("microscope", "properties", "status", "readproperty");
      expect(url).toBe("http://microscope.local/api/v3/status");
    });

    it("thingFormUrl uses absolute href directly if it starts with http", () => {
      const store = useWotStore();
      const url = store.thingFormUrl("microscope", "actions", "toggle", "invokeaction");
      expect(url).toBe("http://external.com/api/v3/toggle");
    });

    it("returns relative href if the Thing Description has no base property", () => {
      const store = useWotStore();
      store.addThingDescription("noBaseThing", {
        properties: {
          status: { forms: [{ op: "readproperty", href: "relative-path-only" }] },
        },
      });

      const url = store.thingFormUrl("noBaseThing", "properties", "status", "readproperty");
      expect(url).toBe("relative-path-only");
    });

    it("normalizes slashes when base lacks trailing slash and href has leading slash", () => {
      const store = useWotStore();
      store.addThingDescription("messyThing", {
        // No trailing slash
        base: "http://microscope.local/api/v3",
        properties: {
          // Extra leading slash
          status: { forms: [{ op: "readproperty", href: "/status" }] },
        },
      });

      const url = store.thingFormUrl("messyThing", "properties", "status", "readproperty");
      expect(url).toBe("http://microscope.local/api/v3/status");
    });

    it("thingPropertyUrl and thingActionUrl proxy correctly", () => {
      const store = useWotStore();
      expect(store.thingPropertyUrl("microscope", "status", "readproperty")).toBe(
        "http://microscope.local/api/v3/status",
      );
      expect(store.thingActionUrl("microscope", "toggle", "invokeaction")).toBe(
        "http://external.com/api/v3/toggle",
      );
    });
  });
});
