import { defineStore } from "pinia";
import { ref, computed } from "vue";
import axios from "axios";

export const useWotStore = defineStore(
  "wot",
  () => {
    // State
    const thingDescriptions = ref({});
    const servient = ref(null);
    const helpers = ref(null);

    // Getters
    const thingList = computed(() => Object.keys(thingDescriptions.value));

    // Actions
    function addThingDescription(thingName, thingDescription) {
      thingDescriptions.value[thingName] = thingDescription;
    }

    function removeThingDescription(thingName) {
      delete thingDescriptions.value[thingName];
    }

    function removeAllThingDescriptions() {
      thingDescriptions.value = {};
    }

    async function fetchThingDescription(uri, name = null) {
      // Fetch the thing description from the given URI and consume it
      // NB this should only be called once, or we'll duplicate effort.
      // Deduplication should be done elsewhere.
      const response = await axios.get(uri);
      const td = response.data;
      const thingName = name || uri.replace(/\/$/, "").split("/").pop();
      addThingDescription(thingName, td);
    }

    async function fetchThingDescriptions(uri) {
      // Fetch thing descriptions from the given URI
      const response = await axios.get(uri);
      if (response.status !== 200) throw "Could not retrieve thing descriptions";
      for (const k in response.data) {
        let thingName = k.replace(/\/$/, "").replace(/^\//, "");
        addThingDescription(thingName, response.data[k]);
      }
    }

    function thingAvailable(thingName) {
      return thingName in thingDescriptions.value;
    }

    function thingAffordanceAvailable(thing, affordanceType, affordance) {
      const td = thingDescriptions.value[thing];
      if (!td || !td[affordanceType]) return false;
      return affordance in td[affordanceType];
    }

    function thingFormUrl(thing, affordanceType, affordance, op, allowUndefined = true) {
      // Find the URL for a particular operation
      const td = thingDescriptions.value[thing];
      if (!td) {
        if (allowUndefined) return undefined;
        throw `Could not find form for ${affordanceType} ${thing}/${affordance} with op ${op}`;
      }
      const affordances = td[affordanceType];
      if (!affordances || !(affordance in affordances)) {
        if (allowUndefined) return undefined;
        throw `Could not find form for ${affordanceType} ${thing}/${affordance} with op ${op}`;
      }
      let href = findFormHref(affordances[affordance], op);
      if (href === undefined) {
        if (allowUndefined) return undefined;
        throw `Could not find form for ${affordanceType} ${thing}/${affordance} with op ${op}`;
      }
      // If we've found an href, prepend the `base` URL if appropriate
      if (href.startsWith("http")) return href;
      if ("base" in td) {
        let base = td.base;
        if (href.startsWith("/")) href = href.slice(1);
        if (!base.endsWith("/")) base += "/";
        return base + href;
      }
      return href;
    }

    function thingPropertyUrl(thing, property, op, allowUndefined) {
      // Find the URL for a particular property
      return thingFormUrl(thing, "properties", property, op, allowUndefined);
    }

    function thingActionUrl(thing, action, op, allowUndefined) {
      // Find the URL for a particular action
      return thingFormUrl(thing, "actions", action, op, allowUndefined);
    }

    return {
      thingDescriptions,
      servient,
      helpers, // State
      thingList,
      thingAvailable, // Helpers
      thingFormUrl,
      thingPropertyUrl,
      thingActionUrl,
      fetchThingDescription,
      fetchThingDescriptions, // Actions
      addThingDescription,
      removeThingDescription,
      removeAllThingDescriptions,
      thingAffordanceAvailable,
    };
  },
  {
    persist: false,
  },
);

export function findFormHref(affordance, op) {
  // Find the form in the affordance that matches the given operation type
  if (affordance === undefined) return undefined;
  let forms = affordance.forms;
  let matchingForm = forms.find((f) => f.op == op || f.op.includes(op));
  if (matchingForm === undefined) return undefined;
  return matchingForm.href;
}
