<template>
  <div>
    <label v-if="useDropdown">
      <span class="uk-form-label" :class="{ 'ofm-highlighted-label': isChangedFomDefault }">
        {{ label }}
      </span>
      <div class="input-and-buttons-container">
        <select
          v-model="internalValue"
          class="uk-form-small property-input dropdown"
          @change="sendValue"
        >
          <option v-for="(value, display) in options" :key="value" :value="value">
            {{ display }}
          </option>
        </select>
        <property-control-menu
          :can-reset="isChangedFomDefault"
          @reset-default="resetDefault"
          @request-update="requestUpdate"
        />
      </div>
    </label>
    <label v-if="!useDropdown && dataType == 'number'">
      <span class="uk-form-label" :class="{ 'ofm-highlighted-label': isChangedFomDefault }">
        {{ label }}
      </span>
      <div class="input-and-buttons-container">
        <div class="uk-inline number-wrapper">
          <input
            ref="numericalInput"
            v-model="internalValue"
            class="uk-form-small property-input numeric-property"
            :class="{ edited: isEdited, flash: animateUpdate }"
            :disabled="isDisabled"
            type="number"
            :min="minimum"
            :max="maximum"
            @input="grabFocus"
            @focusout="focusOut"
            @keydown="keyDown"
            @animationend="animationEnd"
          />
          <div class="spinner-buttons">
            <button type="button" tabindex="-1" @click="increment(1)" @mousedown="spinnerClick">
              ▲
            </button>
            <button type="button" tabindex="-1" @click="increment(-1)" @mousedown="spinnerClick">
              ▼
            </button>
          </div>
        </div>
        <property-control-menu
          :can-reset="isChangedFomDefault"
          @reset-default="resetDefault"
          @request-update="requestUpdate"
        />
      </div>
    </label>
    <div v-if="!useDropdown && dataType == 'boolean'" class="input-and-buttons-container">
      <label class="property-input">
        <input
          ref="checkbox"
          v-model="internalValue"
          :disabled="isDisabled"
          class="uk-checkbox"
          type="checkbox"
          @change="sendValue"
        />
        <span class="uk-form-label" :class="{ 'ofm-highlighted-label': isChangedFomDefault }">
          {{ label }}
        </span>
      </label>
      <property-control-menu
        :can-reset="isChangedFomDefault"
        @reset-default="resetDefault"
        @request-update="requestUpdate"
      />
    </div>
    <label v-if="!useDropdown && dataType == 'number_array'">
      <span class="uk-form-label" :class="{ 'ofm-highlighted-label': isChangedFomDefault }">
        {{ label }}
      </span>
      <div class="input-and-buttons-container">
        <input
          v-for="i in valueLength"
          :key="i"
          v-model="internalValue[i - 1]"
          class="uk-form-small property-input"
          :class="{ edited: isEdited, flash: animateUpdate }"
          :disabled="isDisabled"
          type="number"
          @input="grabFocus"
          @focusout="focusOut"
          @keydown="keyDown"
          @animationend="animationEnd"
        />
        <property-control-menu
          :can-reset="isChangedFomDefault"
          @reset-default="resetDefault"
          @request-update="requestUpdate"
        />
      </div>
    </label>
    <label v-if="!useDropdown && dataType == 'number_object'">
      <span class="uk-form-label" :class="{ 'ofm-highlighted-label': isChangedFomDefault }">
        {{ label }}
      </span>
      <div v-for="(val, key) in modelValue" :key="key">
        <label>{{ internalLabels[key] }}</label>
        <div class="input-and-buttons-container">
          <input
            v-model="internalValue[key]"
            class="uk-form-small property-input"
            :class="{ edited: isEdited, flash: animateUpdate }"
            :disabled="isDisabled"
            type="number"
            @input="grabFocus"
            @focusout="focusOut"
            @keydown="keyDown"
            @animationend="animationEnd"
          />
          <property-control-menu
            :can-reset="isChangedFomDefault"
            @reset-default="resetDefault"
            @request-update="requestUpdate"
          />
        </div>
      </div>
    </label>
    <label v-if="!useDropdown && dataType == 'string'">
      <span class="uk-form-label" :class="{ 'ofm-highlighted-label': isChangedFomDefault }">
        {{ label }}
      </span>
      <div class="input-and-buttons-container">
        <input
          v-model="internalValue"
          class="uk-form-small property-input"
          :class="{ edited: isEdited, flash: animateUpdate }"
          :disabled="isDisabled"
          type="text"
          @focusout="focusOut"
          @keydown="keyDown"
          @animationend="animationEnd"
        />
        <property-control-menu
          :can-reset="isChangedFomDefault"
          @reset-default="resetDefault"
          @request-update="requestUpdate"
        />
      </div>
    </label>
    <label v-if="!useDropdown && dataType == 'other'">
      <span class="uk-form-label" :class="{ 'ofm-highlighted-label': isChangedFomDefault }">
        {{ label }}
      </span>
      <div class="input-and-buttons-container">
        <input
          v-model="internalValue"
          class="uk-form-small property-input"
          type="text"
          disabled="true"
        />
      </div>
    </label>
  </div>
</template>

<script>
import PropertyControlMenu from "./propertyControlMenu.vue";

export default {
  name: "InputFromSchema",

  components: {
    PropertyControlMenu,
  },
  props: {
    dataSchema: {
      type: Object,
      required: true,
    },
    modelValue: {
      type: null,
      required: false,
      default: undefined,
    },
    label: {
      type: String,
      default: "",
    },
    animate: {
      type: Boolean,
      default: null,
    },
    options: {
      type: Object,
      // Default is Null as None is passed from the server.
      default: null,
      required: false,
    },
    step: {
      type: Number,
      default: null,
      required: false,
    },
  },

  emits: ["requestUpdate", "sendValue", "animationShown", "changed"],

  compatConfig: { COMPONENT_V_MODEL: false },

  data() {
    return {
      // Initialise with a copy to try to prevent the this.modelValue prop being mutated if
      // the modelValue is an array or object. For future updates we stringify and parse
      // (see resetInternalValue). If we do this here there is a chance we get errors
      // as internalValue is still null when rendering starts.
      internalValue: Array.isArray(this.modelValue)
        ? [...this.modelValue]
        : typeof this.modelValue === "object"
          ? { ...this.modelValue }
          : this.modelValue,
      // Is edited can't be computed as we mutate internalValue
      isEdited: false,
      animateUpdate: null,
    };
  },
  computed: {
    internalLabels: function () {
      if (this.dataType == "number_object") {
        let labels = {};
        for (const key in this.internalValue) {
          labels[key] = this.dataSchema.properties[key].title;
        }
        return labels;
      }
      return [];
    },
    valueLength: function () {
      if (this.dataType == "number_array") {
        if (this.internalValue == undefined) {
          return 0;
        }
        return this.internalValue.length;
      } else {
        return 1;
      }
    },
    isDisabled() {
      return !this.dataSchema?.forms?.some((form) => form.op?.includes("writeproperty"));
    },
    useDropdown: function () {
      if (this.options === null) return false;
      if (["number", "string", "boolean"].includes(this.dataType)) {
        return true;
      }
      console.warn(`Cannot support dropdown for datatype of ${this.dataType}`);
      return false;
    },
    dataType: function () {
      let prop = this.effectiveSchema;
      if (prop == undefined) {
        return "undefined";
      }
      const num_types = ["integer", "float", "number"];
      if (num_types.includes(prop.type)) {
        return "number";
      }
      if (prop.type === "array") {
        if (num_types.includes(prop.items.type)) {
          return "number_array";
        }
        if (Array.isArray(prop.items)) {
          if (prop.items.every((t) => num_types.includes(t.type))) {
            return "number_array";
          }
        }
      }
      if (prop.type === "boolean") {
        return "boolean";
      }
      if (prop.type === "string") {
        return "string";
      }
      if (prop.type === "object") {
        let numeric = true;
        for (let key in prop.properties) {
          if (!num_types.includes(prop.properties[key].type)) {
            numeric = false;
            break;
          }
        }
        if (numeric) {
          return "number_object";
        }
      }
      return "other";
    },
    maximum() {
      if (this.dataType !== "number") return undefined;
      return this.effectiveSchema.maximum;
    },
    minimum() {
      if (this.dataType !== "number") return undefined;
      return this.effectiveSchema.minimum;
    },
    effectiveSchema() {
      const variants = this.dataSchema?.oneOf || this.dataSchema?.anyOf;
      if (!Array.isArray(variants)) return this.dataSchema;
      const concrete = variants.filter((variant) => variant.type && variant.type !== "null");
      return concrete.length === 1 ? { ...this.dataSchema, ...concrete[0] } : this.dataSchema;
    },
    /**
     * Is the value changed from default (If no default is set this returns false)
     */
    isChangedFomDefault() {
      if (this.dataSchema.default === undefined) return false;
      return this.dataSchema.default !== this.modelValue;
    },
    /**
     * The step size for numerical spinners.
     *
     * If not a number this is `undefined`. If `step` is set as a prop then it is used
     * otherwise `multipleOf` from the property `dataSchema` is used. If `multipleOf`
     * is not set in the `dataSchema` JS will return `undefined` and the browser will
     * use its default value.
     */
    spinner_step() {
      if (this.dataType !== "number") return undefined;
      if (this.step !== null) return this.step;
      return this.effectiveSchema.multipleOf;
    },
  },

  watch: {
    modelValue: {
      deep: true,
      handler() {
        // Fire updateIsEdited on both modelValue and internal modelValue change,
        // as change in modelValue may not cause internalValue to change.
        this.updateIsEdited();
        this.resetInternalValue();
      },
    },
    internalValue: {
      handler() {
        this.updateIsEdited();
      },
    },
    animate(updated) {
      if (updated) {
        this.animateUpdate = true;
      }
    },
    isChangedFomDefault: {
      immediate: true,
      handler(val) {
        this.$emit("changed", val);
      },
    },
  },

  mounted() {
    if (this.modelValue !== undefined) {
      this.resetInternalValue();
    }
  },

  methods: {
    resetInternalValue: function () {
      // Whenever updatirng th internal modelValue stringify and parse as a form of deepcopy.
      // This ensure that the this.modelValue prop is not mutated for when elements of arrays
      // or objects are updated.
      this.internalValue = JSON.parse(JSON.stringify(this.modelValue));
    },
    requestUpdate: async function () {
      this.internalValue = this.modelValue;
      this.$emit("requestUpdate");
    },
    resetDefault: async function () {
      if (this.isChangedFomDefault) {
        this.$emit("sendValue", this.dataSchema.default);
      }
    },
    sendValue: async function () {
      this.$emit("sendValue", this.internalValue);
    },
    checkboxUpdated: function () {
      if (this.internalValue != this.$refs.checkbox.checked) {
        this.internalValue = this.$refs.checkbox.checked;
        this.sendValue();
      }
    },
    increment(sign) {
      const step = this.spinner_step || 1;
      const multipleOf = this.dataSchema.multipleOf;
      let newValue = Number(this.internalValue) || 0;
      newValue += sign * step;
      // Apply minimum if defined
      if (this.minimum !== undefined && this.minimum !== null) {
        newValue = Math.max(newValue, this.minimum);
      }
      // Apply maximum if defined
      if (this.maximum !== undefined && this.maximum !== null) {
        newValue = Math.min(newValue, this.maximum);
      }

      // If multipleOf is validated on server then enforce it.
      if (multipleOf) {
        newValue = Math.round(newValue / multipleOf) * multipleOf;
      }

      // Rounding to about 5 sig figs to stop weird javascript rounding.
      const absVal = Math.abs(newValue);
      const magnitude = absVal > Number.EPSILON ? Math.floor(Math.log10(absVal)) : 0;
      const factor = 10 ** -(Math.floor(magnitude) - 4);
      this.internalValue = Math.round(newValue * factor) / factor;
    },
    /**
     * Grab focus for the numerical input when a spinner is clicked.
     */
    spinnerClick(event) {
      event.preventDefault();
      this.$refs.numericalInput.focus();
    },
    /** Grab the browser focus.
     *
     * This is needed for numerical elements to be in focus when a spinner
     * is clicked so that the data sends when focus is lost.
     */
    grabFocus(event) {
      event.target.focus();
      // This is needed for number objects and number arrays
      this.updateIsEdited();
    },
    focusOut: function (event) {
      if (this.modelValue != event.target.value) {
        this.sendValue(event.target.value);
      }
    },
    keyDown: function (event) {
      // Pressing enter should set the property, whether or not we think it's changed.
      if (event.keyCode == 13) {
        this.sendValue();
      }
      // If numeric then capture Up and Down keys for incrementing.
      if (this.dataType === "number") {
        if (event.key === "ArrowUp") {
          event.preventDefault();
          this.increment(1);
        }
        if (event.key === "ArrowDown") {
          event.preventDefault();
          this.increment(-1);
        }
      }
    },
    updateIsEdited: function () {
      this.isEdited =
        this.deepStringify(this.internalValue) !== this.deepStringify(this.modelValue);
    },
    animationEnd: function () {
      this.animateUpdate = null;
      this.$emit("animationShown");
    },
    deepStringify: function (val) {
      // Create a json string where all internal numbers are also JSON strings. This is
      // needed to robustly check if the modelValue is updated because the raw modelValue may be a
      // number but anything typed in the input is a string. In the case of arrays or
      // objects even with JSON.stringify we end up comparing ["1", 3] with [1, 3] and
      // find them as not equal.
      if (Array.isArray(val)) {
        return JSON.stringify(val.map(String));
      }
      if (val && typeof val === "object") {
        const normalized = Object.fromEntries(Object.entries(val).map(([k, v]) => [k, String(v)]));
        return JSON.stringify(normalized);
      }
      return JSON.stringify(String(val));
    },
  },
};
</script>

<style scoped>
.input-and-buttons-container {
  display: flex;
  flex-flow: row wrap;
  place-content: stretch flex-start;
  align-items: center;
  width: 100%;
}

.property-input {
  flex-grow: 1;
  margin-left: 5px;
  margin-right: 5px;
  width: 6em;
}

/* Hide standard spinners as it isn't possible to customise the browser ones. */
.numeric-property {
  appearance: textfield;
}

.numeric-property::-webkit-outer-spin-button,
.numeric-property::-webkit-inner-spin-button {
  appearance: none;
}

.number-wrapper {
  display: flex;
  flex-flow: row wrap;
  place-content: stretch flex-start;
  align-items: center;
  flex-grow: 1;
}

.spinner-buttons {
  position: absolute;
  right: 4px;
  top: 1px;
  bottom: 5px;
  display: flex;
  flex-direction: column;
  justify-content: space-between;
}

.spinner-buttons button {
  height: 45%;
  padding: 0 6px;
  line-height: 1;
  font-size: 10px;
  border: none;
  background: transparent;
  cursor: pointer;
  color: #888;
}

.dropdown {
  background-color: #fff;
  border: 1px solid #ccc;
  opacity: 1;
}

.edited {
  background-color: #fff3cd;
}

@keyframes green-flash {
  0% {
    background-color: #3fda63;
  }

  100% {
    background-color: white;
  }
}

.flash {
  animation: green-flash 0.7s ease;

  /* Without this background-colour chrome will ignore the animation colour. */
  background-color: white;
}
</style>
