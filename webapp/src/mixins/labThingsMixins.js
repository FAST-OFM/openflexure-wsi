/**
 * @fileoverview
 * Global mixin providing access to the LabThings (WoT) API.
 *
 * This mixin is registered globally in `main.js` using. Do not
 * manually import it in components.
 */
import axios from "axios";
import { useWotStore } from "@/stores/wot.js";
import { useSettingsStore } from "@/stores/settings.js";
import { mapState, mapStores } from "pinia";

export default {
  data() {
    return {
      pollTimers: {},
    };
  },

  computed: {
    ...mapState(useSettingsStore, ["baseUri"]),
    ...mapStores(useWotStore),
  },

  methods: {
    thingDescription(thing) {
      return this.wotStore.thingDescriptions[thing];
    },
    thingList() {
      return this.wotStore.thingList();
    },
    thingAvailable(thing) {
      return this.wotStore.thingAvailable(thing);
    },
    thingPropertyUrl(thing, property, allowUndefined = false) {
      return this.wotStore.thingPropertyUrl(thing, property, "readproperty", allowUndefined);
    },
    thingActionUrl(thing, action, allowUndefined = false) {
      return this.wotStore.thingActionUrl(thing, action, "invokeaction", allowUndefined);
    },
    thingActionAvailable(thing, action) {
      return this.wotStore.thingAffordanceAvailable(thing, "actions", action);
    },
    thingPropertyAvailable(thing, property) {
      return this.wotStore.thingAffordanceAvailable(thing, "properties", property);
    },
    async readThingProperty(thing, property, silenceErrors = false) {
      let url = this.wotStore.thingPropertyUrl(thing, property, "readproperty", false);
      try {
        let response = await axios.get(url);
        return response.data;
      } catch (error) {
        if (!silenceErrors) this.modalError(error);
        return undefined;
      }
    },
    async writeThingProperty(thing, property, value) {
      let url = this.wotStore.thingPropertyUrl(thing, property, "writeproperty", false);
      // Property endpoints take JSON, including scalar strings and booleans.
      // Serialize explicitly so JSON-looking strings retain their string type.
      await axios.put(url, JSON.stringify(value), {
        headers: { "Content-Type": "application/json" },
      });
    },
    async invokeAction(thing, action, data, handleErrors = true) {
      let url = this.thingActionUrl(thing, action);
      try {
        let response = await axios.post(url, data);
        return response;
      } catch (error) {
        if (handleErrors) {
          this.modalError(error);
          return undefined;
        } else {
          throw error;
        }
      }
    },
    async pollUntilComplete(
      taskUrl,
      ongoingMethod,
      finalMethod,
      interval = 500,
      modalErrors = true,
    ) {
      let response;
      let finalMethodCalled = false;
      try {
        response = await axios.get(taskUrl, { baseURL: this.baseUri });
        const result = response.data.status;

        if (result === "running" || result === "pending") {
          ongoingMethod?.(response);
          this.pollTimers[taskUrl] = setTimeout(() => {
            this.pollUntilComplete(taskUrl, ongoingMethod, finalMethod, interval);
          }, interval);
        } else {
          clearTimeout(this.pollTimers[taskUrl]);
          delete this.pollTimers[taskUrl];
          finalMethodCalled = true;
          finalMethod?.(response);
        }
      } catch (error) {
        this.$emit("error", error);
        if (modalErrors) {
          this.modalError(error);
        }
        clearTimeout(this.pollTimers[taskUrl]);
        delete this.pollTimers[taskUrl];
        if (!finalMethodCalled) {
          finalMethod?.(response);
        }
      }
    },
    terminateAction(taskUrl) {
      axios.delete(taskUrl, { baseURL: this.baseUri });
    },
    async findOngoingActions(thing, action) {
      let url = this.thingActionUrl(thing, action);
      try {
        return await axios.get(url);
      } catch (error) {
        console.warn("checkExistingTasks: request failed", error);
        return null;
      }
    },
    /**
     * Find and ongoing Action and return its task response.
     *
     * Returns null if there is no ongoing action
     */
    async getOngoingAction(thing, action) {
      let response = await this.findOngoingActions(thing, action);
      // Exit if response is null, due to an error.
      if (response == null) return null;
      // Check for a task that is ongoing.
      // We can't handle multiple tasks ongoing, so this picks the first.
      // ?? Is the "Nullish Coalescing-Operator" to turn undefined into null.
      return response.data.find((t) => ["pending", "running"].includes(t.status)) ?? null;
    },

    async getThingEndpoint(thing, endpoint) {
      let url = `${this.baseUri}/${thing}/${endpoint}`;
      try {
        const response = await axios.get(url);
        return response.data;
      } catch {
        return undefined;
      }
    },
  },
};
