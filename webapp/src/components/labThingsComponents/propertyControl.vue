<template>
  <input-from-schema
    v-if="!isBroken"
    v-model="modelValue"
    :data-schema="propertyDescription"
    :label="label"
    :animate="animate"
    :options="options"
    :step="step"
    @request-update="readProperty"
    @send-value="writeProperty"
    @animation-shown="resetAnimate"
    @changed="$emit('changed', $event)"
  />
  <div
    v-else
    class="ui-element-broken"
    :title="`${thingName} has no property &quot;${propertyName}&quot;.`"
  >
    <span class="material-symbols-outlined ui-element-error-icon"> error </span> {{ label }}
  </div>
</template>

<script>
import { formatValue } from "@/js_utils/formatter.mjs";
import InputFromSchema from "./inputFromSchema.vue";

export default {
  name: "PropertyControl",

  components: {
    InputFromSchema,
  },

  props: {
    label: {
      type: String,
      default: "",
    },
    propertyName: {
      type: String,
      required: true,
    },
    thingName: {
      type: String,
      required: true,
    },
    readBack: {
      type: Boolean,
      required: false,
      default: false,
    },
    readBackDelay: {
      type: Number,
      default: 1000,
      required: false,
    },
    options: {
      type: Object,
      default: null,
      required: false,
    },
    step: {
      type: Number,
      default: null,
      required: false,
    },
    isBroken: {
      type: Boolean,
      required: false,
      default: false,
    },
  },

  emits: ["changed", "saved"],

  data() {
    return {
      modelValue: undefined,
      animate: false,
    };
  },

  computed: {
    propertyDescription: function () {
      const td = this.wotStore.thingDescriptions[this.thingName];
      // Return `undefined` if the thing doesn't exist or has no properties
      if (!td || !td.properties) return undefined;
      // JS returns `undefined` if this property name doesn't exist
      return td.properties[this.propertyName];
    },
  },

  watch: {
    propertyDescription: function () {
      // Ensure we read the property once the URL is known
      this.readProperty();
    },
  },

  mounted: function () {
    // Read the property when we're mounted - usually this won't
    // work because the URL isn't set yet. However, it's helpful if
    // the app is reloaded (e.g. from a dev server).
    if (this.modelValue == undefined) {
      this.readProperty();
    }
  },

  methods: {
    readProperty: async function () {
      if (this.isBroken) return;
      let data = await this.readThingProperty(this.thingName, this.propertyName);
      this.modelValue = data;
      return data;
    },
    writeProperty: async function (requestedValue) {
      try {
        this.modelValue = requestedValue;
        await this.writeThingProperty(this.thingName, this.propertyName, requestedValue);
        if (this.readBack) {
          await new Promise((r) => setTimeout(r, this.readBackDelay));
          let newVal = await this.readProperty();
          if (newVal == requestedValue) {
            this.animate = true;
          } else {
            this.animate = true;
            await this.modalNotify(
              `Set ${this.label} to ${formatValue(
                newVal,
              )} (closest valid value to requested ${formatValue(requestedValue)}).`,
            );
          }
        } else {
          this.animate = true;
        }
        this.$emit("saved");
      } catch (error) {
        // Use mixin to display error
        this.modalError(error);
        // Re-read property to try to update to server value
        this.readProperty();
      }
    },
    resetAnimate: function () {
      this.animate = false;
    },
  },
};
</script>

<style scoped></style>
