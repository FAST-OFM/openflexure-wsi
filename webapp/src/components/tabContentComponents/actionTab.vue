<!-- A tab that changes its sidebar when an action is started.

## Overview

This is a generic component used to build tabs that need to switch view when an action is started,
such as the Slide Scan tab.

The task runs in 3 modes:

* **Unstarted** - No task has been started.
* **Running** - The task is running.
* **Completed** - The task has completed.

## HTML Slots and Template

The template has 3 slots.

* The main slot is the main view on the right hand side of the tab. To display the microscope stream
    use `<streamDisplay />` here
* **controls** - A named slot for the controls that are shown in the sidebar in **Unstarted** mode.
* **task-info** - A slot for any information or controls to show at the bottom of the sidebar in
    **Running** or **Completed** mode.

In **Running** or **Completed** modes the sidebar automatically shows:

* **A tile** if `taskInfoTitle` prop is set.
* **A mini stream** if `taskInfoStream` prop is `true`
* **A log view**
* **A cancel button** or a **Close** button depending on if the mode is **Running** or **Completed**

## Changing mode

To notify the tab that the action running the `taskId` and `taskUrl` props should be set. These values
are the data send from the `taskStarted` event of `actionButton`.

**Note:** This component will check whether the task has been started externally (for example, by
another window). A `actionStartedExternally` event with `taskId` and `taskUrl` data will be emitted in
this case. The tab does not mutate its own props so it the parent component should subscribe to the
event and set the props accordingly.

When the task completes a `completed` event is emitted. No action is needed on this event, it is emitted
so that the parent component can update any other data or UI elements if needed.

When the close button is clicked a `closeTask` event is emitted. It is the responsibility of the parent
component to set the `taskId` and `taskUrl` props back to `null`, returning the tab to **Unstarted**
mode.
-->
<template>
  <div
    ref="actionTab"
    uk-grid
    class="uk-grid-collapse uk-height-1-1 uk-margin-remove uk-padding-remove"
  >
    <div class="control-component">
      <div v-if="!taskStarted">
        <slot name="controls"></slot>
      </div>
      <div v-if="taskStarted">
        <h2 v-if="taskInfoTitle" style="text-align: center">{{ taskInfoTitle }}</h2>
        <mini-stream-display v-if="taskInfoStream" :stream-id="setStreamId" />
        <action-log-display id="log-display" :log="log" :task-status="taskStatus" />
        <div class="uk-width-1-1 uk-flex uk-flex-center">
          <div class="uk-width-2-3">
            <action-button
              v-if="taskRunning"
              :thing="thing"
              :action="action"
              :force-id="taskId"
              :force-url="taskUrl"
              submit-label=""
              :cancel-label="cancelLabel"
              :can-terminate="true"
              @completed="$emit('completed')"
              @finished="$emit('finished')"
              @update:task-status="taskStatus = $event"
              @update:progress="progress = $event"
              @update:log="log = $event"
            />
            <button
              v-if="!taskRunning"
              type="button"
              class="uk-button uk-width-1-1 uk-position-relative"
              @click="closeTask"
            >
              Close
            </button>
          </div>
        </div>
        <slot name="task-info"></slot>
      </div>
    </div>
    <div class="main-view uk-width-expand uk-height-1-1">
      <slot></slot>
    </div>
  </div>
</template>

<script>
import actionLogDisplay from "../labThingsComponents/actionLogDisplay.vue";
import MiniStreamDisplay from "../genericComponents/miniStreamDisplay.vue";
import ActionButton from "../labThingsComponents/actionButton.vue";
import { useIntersectionObserver } from "@vueuse/core";

export default {
  name: "ActionTab",

  components: {
    actionLogDisplay,
    MiniStreamDisplay,
    ActionButton,
  },

  props: {
    action: {
      type: String,
      required: true,
    },
    thing: {
      type: String,
      required: true,
    },
    taskId: {
      type: [String, null],
      required: true,
    },
    taskUrl: {
      type: [String, null],
      required: true,
    },
    taskInfoTitle: {
      type: String,
      required: false,
      default: null,
    },
    taskInfoStream: {
      type: Boolean,
      required: false,
      default: false,
    },
    cancelLabel: {
      type: String,
      required: false,
      default: "Cancel",
    },
  },

  emits: ["closeTask", "completed", "finished", "actionStartedExternally"],

  data() {
    return {
      taskStatus: "pending",
      progress: null,
      log: [],
      pollTimer: null,
      setStreamId: this.$options.name,
    };
  },

  computed: {
    taskStarted() {
      return this.taskId && this.taskUrl;
    },
    taskRunning() {
      return (this.taskStatus == "running") | (this.taskStatus == "pending");
    },
  },

  mounted() {
    useIntersectionObserver(
      this.$refs.actionTab,
      ([{ isIntersecting }]) => {
        this.visibilityChanged(isIntersecting);
      },
      {
        threshold: 0.0, // Adjust as needed
      },
    );
  },

  methods: {
    /**
     * Reset the data needed for actions to be polled.
     */
    resetData() {
      this.taskStatus = "pending";
      this.log = [];
    },
    closeTask() {
      // Reset task status to pending so that it is re-read next time an action starts.
      this.resetData();
      this.$emit("closeTask");
    },
    visibilityChanged(isVisible) {
      this.isVisible = isVisible;

      if (this.isVisible) {
        this.startTimer();
      } else {
        this.stopTimer();
      }
    },
    startTimer() {
      if (this.pollTimer) return;

      this.pollTimer = setInterval(() => {
        this.checkIfStartedExternally();
      }, 1000);
    },
    stopTimer() {
      if (!this.pollTimer) return;

      clearInterval(this.pollTimer);
      this.pollTimer = null;
    },
    async checkIfStartedExternally() {
      if (!this.taskStarted | !this.taskRunning) {
        const ongoingTask = await this.getOngoingAction(this.thing, this.action);
        if (ongoingTask) {
          this.resetData();
          const taskUrl = ongoingTask.links.find((t) => t.rel == "self").href;
          this.$emit("actionStartedExternally", ongoingTask.id, taskUrl);
        }
      }
    },
  },
};
</script>

<style scoped>
.control-component {
  width: 33%;
}
</style>
