<template>
  <div>
    <p v-if="labelText !== undefined" class="toggle-label uk-form-label">{{ labelText }}</p>
    <div class="toggle-switch" :class="{ checked: modelValue }" @click="toggle" />
    <p v-if="showStateLabel" class="toggle-state-label">{{ stateLabelText }}</p>
  </div>
</template>

<script>
export default {
  name: "ToggleSwitch",

  props: {
    modelValue: {
      required: true,
      type: Boolean,
    },
    labelText: {
      required: false,
      type: String,
      default: undefined,
    },
    falseLabel: {
      required: false,
      type: String,
      default: undefined,
    },
    trueLabel: {
      required: false,
      type: String,
      default: undefined,
    },
  },

  emits: ["update:modelValue"],

  computed: {
    showStateLabel() {
      return this.trueLabel !== undefined && this.falseLabel !== undefined;
    },
    stateLabelText() {
      return this.modelValue ? this.trueLabel : this.falseLabel;
    },
  },

  methods: {
    toggle() {
      this.$emit("update:modelValue", !this.modelValue);
    },
  },
};
</script>

<style lang="less" scoped>
@import url("../../assets/less/variables.less");

.toggle-label {
  margin-bottom: 0.2rem;
}

.toggle-state-label {
  margin-top: 0.2rem;
  font-style: italic;
}

.toggle-switch {
  background: #ddd;
  border-radius: 12px;
  box-shadow: inset 1px 1px 1px rgba(0, 0, 0, 0.08);
  cursor: pointer;
  flex: none;
  height: 24px;
  position: relative;
  transition: background-color 150ms;
  width: 48px;
}

.toggle-switch::before {
  background: #fff;
  background-image: radial-gradient(
    circle at 6px 6px,
    rgba(0, 0, 0, 0) 0,
    rgba(0, 0, 0, 0.05) 16px
  );
  border-radius: 10px;
  box-shadow: 1px 1px 1px rgba(0, 0, 0, 0.08);
  content: "";
  display: block;
  height: 20px;
  left: 2px;
  position: absolute;
  top: 2px;
  transition: left 150ms;
  width: 20px;
  will-change: left;
}

.checked {
  background-color: @global-primary-background;
}

.checked::before {
  background-image: radial-gradient(
    circle at 6px 6px,
    rgba(0, 0, 0, 0) 0,
    rgba(0, 0, 0, 0.05) 16px
  );
  left: 26px;
}

.toggle-switch:hover {
  box-shadow: inset 1px 1px 1px rgba(0, 0, 0, 0.2);
}

.toggle-switch:hover::before {
  background-image: radial-gradient(
    circle at 6px 6px,
    rgba(0, 0, 0, 0) 0,
    rgba(0, 0, 0, 0.1375) 16px
  );
  box-shadow: 1px 1px 1px rgba(0, 0, 0, 0.2);
}
</style>
