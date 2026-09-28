<template>
  <div class="uk-padding-remove">
    <button
      ref="actionButton"
      type="button"
      :title="tooltipText"
      :disabled="buttonDisabled"
      class="uk-button uk-width-1-1 uk-position-relative"
      :class="buttonClasses"
      @click="handleClick"
    >
      <action-progress-bar :progress="progress" :task-status="taskStatus" :in-button="true" />
      <span v-if="isBroken" class="material-symbols-outlined ui-element-error-icon"> error </span
      >{{ buttonLabel }}
    </button>

    <action-status-modal
      ref="statusModal"
      :title="submitLabel"
      :log="log"
      :progress="progress"
      :can-terminate="canTerminate"
      :task-running="taskRunning"
      :task-started="taskStarted"
      :task-status="taskStatus"
      :display-stream="modalProgress && streamWithModal"
      @terminate-task="terminateTask"
      @action-modal-closed="$emit('actionModalClosed')"
    />
  </div>
</template>

<script>
import ActionProgressBar from "./actionProgressBar.vue";
import ActionStatusModal from "./actionStatusModal.vue";
import { eventBus } from "../../eventBus.js";
import { useIntersectionObserver } from "@vueuse/core";

export default {
  name: "ActionButton",

  components: {
    ActionProgressBar,
    ActionStatusModal,
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
    // forceId and forceUrl can be used if it is known that when this button is mounted
    // the action should be running.
    forceId: {
      type: [String, null],
      required: false,
      default: null,
    },
    forceUrl: {
      type: [String, null],
      required: false,
      default: null,
    },
    submitData: {
      type: [Object, Array],
      required: false,
      default: () => ({}),
    },
    pollInterval: {
      type: Number,
      required: false,
      default: 1,
    },
    submitLabel: {
      type: String,
      required: false,
      default: "Submit",
    },
    cancelLabel: {
      type: String,
      required: false,
      default: "Cancel",
    },
    canTerminate: {
      type: Boolean,
      required: false,
      default: true,
    },
    requiresConfirmation: {
      type: Boolean,
      required: false,
      default: false,
    },
    confirmationMessage: {
      type: String,
      required: false,
      default: "Start task?",
    },
    buttonPrimary: {
      type: Boolean,
      required: false,
      default: true,
    },
    submitOnEvent: {
      type: String,
      required: false,
      default: null,
    },
    modalProgress: {
      type: Boolean,
      required: false,
      default: false,
    },
    isDisabled: {
      type: Boolean,
      required: false,
      default: false,
    },
    isBroken: {
      type: Boolean,
      required: false,
      default: false,
    },
    streamWithModal: {
      type: Boolean,
      required: false,
      default: false,
    },
  },

  emits: [
    "update:progress",
    "update:taskStarted",
    "update:taskRunning",
    "update:log",
    "update:taskStatus",
    "submit",
    "taskStarted",
    "taskRunning",
    "response",
    "completed",
    "cancelled",
    "finished",
    "error",
    "actionModalClosed",
  ],

  data: function () {
    return {
      taskUrl: null,
      progress: null,
      taskStarted: false,
      taskRunning: false,
      log: [],
      taskStatus: "",
    };
  },

  computed: {
    buttonDisabled() {
      return this.isDisabled || this.isBroken || (this.taskRunning && !this.canTerminate);
    },
    buttonLabel() {
      // If the action is running (and can terminate) then show the cancel label, otherwise
      // show the submit label.
      return this.taskRunning && this.canTerminate ? this.cancelLabel : this.submitLabel;
    },
    buttonClasses() {
      const classes = [];
      if (this.buttonDisabled) {
        classes.push("uk-button-disabled");
      }
      if (this.isBroken) {
        classes.push("ui-element-broken");
      }
      if (this.taskRunning && this.canTerminate) {
        classes.push("uk-button-danger");
      } else if (this.buttonPrimary) {
        classes.push("uk-button-primary");
      } else {
        classes.push("uk-button-default");
      }
      return classes.join(" ");
    },
    tooltipText() {
      return this.isBroken ? `${this.thing} has no action "${this.action}".` : "";
    },
  },

  watch: {
    progress: {
      handler(newval) {
        this.$emit("update:progress", newval);
      },
    },
    taskStarted: {
      handler(newval) {
        this.$emit("update:taskStarted", newval);
      },
    },
    taskRunning: {
      handler(newval) {
        this.$emit("update:taskRunning", newval);
      },
    },
    log: {
      handler(newval) {
        this.$emit("update:log", newval);
      },
      deep: true,
    },
    taskStatus: {
      handler(newval) {
        this.$emit("update:taskStatus", newval);
      },
    },
  },

  mounted() {
    useIntersectionObserver(
      this.$refs.actionButton,
      ([{ isIntersecting }]) => {
        this.visibilityChanged(isIntersecting);
      },
      {
        threshold: 0.0, // Adjust as needed
      },
    );

    if (this.forceId && this.forceUrl) {
      this.taskStarted = true;
      this.startPollingTask(this.forceId, this.forceUrl);
    } else {
      // Check for already running tasks
      if (this.taskStarted != true) {
        this.checkExistingTasks();
      }
      // A global signal listener to perform the action
      if (this.submitOnEvent) {
        eventBus.on(this.submitOnEvent, this.handleShortcutTrigger);
      }
    }
  },

  beforeUnmount() {
    if (this.submitOnEvent) {
      eventBus.off(this.submitOnEvent, this.handleShortcutTrigger);
    }
  },

  methods: {
    handleShortcutTrigger() {
      if (this.isDisabled || this.isBroken) return;
      this.bootstrapTask();
    },
    handleClick() {
      if (this.taskStarted) {
        this.terminateTask();
      } else {
        this.bootstrapTask();
      }
    },
    visibilityChanged(isVisible) {
      if (isVisible && this.taskStarted != true) {
        this.checkExistingTasks();
      }
    },

    /* Check if an existing task had already started when this mounts.
     *
     * This is called on mounted.
     *
     * It will emit taskStarted if it finds an ongoing task to allow parent components
     * to act as expected if the task is started. It will then poll the action.
     *
     */
    async checkExistingTasks() {
      if (this.isBroken) return;
      const ongoingTask = await this.getOngoingAction(this.thing, this.action);
      if (ongoingTask) {
        // There is a started task
        this.taskStarted = true;
        // Find its URL
        const taskUrl = ongoingTask.links.find((t) => t.rel == "self").href;
        this.$emit("taskStarted", ongoingTask.id, taskUrl);
        this.startPollingTask(ongoingTask.id, taskUrl);
      }
    },

    bootstrapTask: function () {
      // Starts the process of creating a new Action task
      if (this.requiresConfirmation) {
        this.modalConfirm(this.confirmationMessage).then(
          () => {
            this.startTask();
          },
          () => {},
        );
      } else {
        this.startTask();
      }
    },

    async startTask() {
      // Starts a new Action task
      this.$emit("submit", this.submitData);
      // Send a request to start a task
      this.taskStarted = true;
      let response;
      try {
        response = await this.invokeAction(
          this.thing,
          this.action,
          this.submitData,
          false, // Stop invokeAction handling the error.
        );
      } catch (error) {
        this.$emit("error", error);
        this.onTaskEnd();
        return;
      }
      // Wait until after the task is acknowledged before confirming to parent
      // component that the task is started.
      this.$emit("taskStarted", response.data.id, response.data.href);
      if (this.modalProgress) {
        this.$refs.statusModal.show();
      }
      // This just starts the polling. No need to await it.
      this.startPollingTask(response.data.id, response.data.href);
    },

    async startPollingTask(taskId, taskUrl) {
      // Return if taskRunning already set.
      if (this.taskRunning) return;
      // Starts polling an existing Action task
      this.taskUrl = taskUrl;
      // Start the store polling TaskId for success
      this.taskRunning = true;
      this.$emit("taskRunning", taskId);
      this.pollUntilComplete(
        taskUrl,
        this.onPollingResponse,
        this.onTaskEnd, // Method to run after task (even if error)
        500, // Interval
      );
    },

    onTaskEnd: function (response) {
      if (response) {
        this.taskStatus = response.data.status;
        this.log = response.data.log;
        if (response.data.status == "completed") {
          this.$emit("response", response.data);
          this.$emit("completed", response.data.output);
        } else if (response.data.status == "cancelled") {
          this.$emit("cancelled", response.data);
        } else if (response.data.status == "error") {
          const err_msg = this.log?.[0]?.message ?? "Unknown error";
          this.$emit("error", err_msg);
        }
      }
      this.taskUrl = null;
      this.taskRunning = false;
      this.taskStarted = false;
      this.$emit("finished");
    },

    onPollingResponse(response) {
      // While the task is still running, we update progress/log,
      // and schedule another poll
      var result = response.data.status;
      this.taskStatus = result;
      this.progress = response.data.progress;
      this.log = response.data.log;
    },

    terminateTask: function () {
      if (this.taskUrl) {
        this.terminateAction(this.taskUrl);
      }
    },
  },
};
</script>
