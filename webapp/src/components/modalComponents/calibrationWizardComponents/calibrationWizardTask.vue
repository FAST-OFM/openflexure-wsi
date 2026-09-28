<!-- The base component for a wizard task.

A task should be used for higher level groupings of individual steps in the wizard,
such as calibrating a Thing. Tasks can be divided into multiple steps.

## How to use this component:

*To make a task with lots of steps:*
Create a task component for a `Thing`, that wraps just this component and passes in
a list of steps. See `cameraCalibrationTask.vue` and `cameraStageMappingTask.vue`
as examples.

*To make a simple task with just one step:*
See `singleStepTask.vue`. This is what is used to display the welcome task.

## How not to use this component:

Do not import this directly into CalibrationWizard as the list of tasks will then
also need props to be passed with the list of step components and step props, and it
gets very confusing.
-->
<template>
  <div>
    <h3 v-if="title">{{ title }}</h3>
    <component
      v-bind="currentStep.props"
      :is="currentStep.component"
      v-if="currentStep"
      :key="stepIndex"
      @awaiting-user="handleAwaitingUser"
      @prevent-navigation="handlePreventNavigation"
      @advance="nextStep"
    />
    <p class="uk-text-right">
      <button
        v-if="showBackButton"
        class="uk-button uk-button-default"
        :class="{ 'uk-button-disabled': preventNavigation, 'disabled-cursor': preventNavigation }"
        type="button"
        @click="previousStep"
      >
        Back
      </button>
      <button
        class="uk-button uk-button-primary uk-margin-left"
        :class="{ 'uk-button-disabled': preventNavigation, 'disabled-cursor': preventNavigation }"
        type="button"
        @click="nextStep"
      >
        {{ nextButtonText }}
      </button>
    </p>
  </div>
</template>

<script>
export default {
  name: "CalibrationWizardTask",

  props: {
    title: {
      type: String,
      default: null,
    },
    first: Boolean,
    final: Boolean,
    startOnLast: {
      type: Boolean,
      default: false,
    },
    steps: {
      type: Array,
      required: true,
    },
  },

  emits: ["back", "next"],

  data() {
    return {
      stepIndex: this.startOnLast ? this.steps.length - 1 : 0,
      stepAwaitingUser: false,
      preventNavigation: false,
    };
  },

  computed: {
    currentStep() {
      return this.steps[this.stepIndex] || null;
    },
    showBackButton() {
      return !this.first || this.stepIndex > 0;
    },
    nextButtonText() {
      const isLastStep = this.final && this.stepIndex === this.steps.length - 1;
      if (this.stepAwaitingUser) {
        return isLastStep ? "Skip and Finish" : "Skip";
      }
      return isLastStep ? "Finish" : "Next";
    },
  },

  methods: {
    /*
     * Move to the previous step in this task, or the previous task if first step.
     */
    previousStep: function () {
      if (this.preventNavigation) return;
      this.stepAwaitingUser = false;
      if (this.stepIndex > 0) {
        this.stepIndex = this.stepIndex - 1;
      } else {
        this.$emit("back");
      }
    },

    /*
     * Move to the next step in this task, or the next task if final step.
     */
    nextStep: function () {
      if (this.preventNavigation) return;
      this.stepAwaitingUser = false;
      if (this.stepIndex < this.steps.length - 1) {
        this.stepIndex = this.stepIndex + 1;
        return true;
      } else {
        this.$emit("next");
      }
    },
    handleAwaitingUser(isAwaiting) {
      this.stepAwaitingUser = isAwaiting;
    },
    handlePreventNavigation(blocked) {
      this.preventNavigation = blocked;
    },
  },
};
</script>
<style lang="less" scoped>
.disabled-cursor {
  cursor: not-allowed !important ;
}
</style>
