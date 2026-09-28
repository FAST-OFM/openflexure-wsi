<template>
  <div ref="modal" class="" uk-modal="bg-close: false; esc-close: false; stack: true;">
    <div id="status-modal" class="uk-modal-dialog uk-modal-body uk-margin-auto-vertical">
      <h2>{{ title }}</h2>
      <mini-stream-display
        v-if="displayStream"
        :stream-id="setStreamId"
        class="uk-margin-small-bottom"
      />
      <action-log-display :log="log" :task-status="taskStatus" />
      <div id="progress-and-cancel-row">
        <div class="stretchy">
          <action-progress-bar :progress="progress" :task-status="taskStatus" />
        </div>

        <button
          v-if="canTerminate && taskRunning"
          type="button"
          class="uk-button uk-button-danger not-stretchy"
          @click="$emit('terminateTask')"
        >
          Cancel
        </button>
        <button v-if="!taskStarted" type="button" class="uk-button not-stretchy" @click="hide">
          Close
        </button>
      </div>
    </div>
  </div>
</template>

<script>
import UIkit from "uikit";
import ActionProgressBar from "./actionProgressBar.vue";
import ActionLogDisplay from "./actionLogDisplay.vue";
import miniStreamDisplay from "../genericComponents/miniStreamDisplay.vue";

export default {
  name: "ActionStatusModal",
  components: {
    ActionProgressBar,
    ActionLogDisplay,
    miniStreamDisplay,
  },

  props: {
    title: {
      type: String,
      required: true,
    },
    log: {
      type: Array,
      required: true,
    },
    progress: {
      type: Number,
      required: false,
      default: null,
    },
    canTerminate: {
      type: Boolean,
      required: true,
    },
    taskRunning: {
      type: Boolean,
      required: true,
    },
    taskStarted: {
      type: Boolean,
      required: true,
    },
    taskStatus: {
      type: String,
      required: true,
    },
    displayStream: {
      type: Boolean,
      required: false,
      default: false,
    },
  },

  emits: ["terminateTask", "actionModalClosed"],

  data() {
    return {
      // This adds the parent name as value for prop streamId
      setStreamId: this.$options.name,
      modal: null,
      modalHiddenHandler: null,
    };
  },

  mounted() {
    this.modal = UIkit.modal(this.$refs.modal);

    this.modalHiddenHandler = () => {
      this.$emit("actionModalClosed");
    };

    this.modal.$el.addEventListener("hidden", this.modalHiddenHandler);
  },

  beforeUnmount() {
    if (this.modal?.$el && this.modalHiddenHandler) {
      this.modal.$el.removeEventListener("hidden", this.modalHiddenHandler);
    }
  },

  methods: {
    show() {
      UIkit.modal(this.$refs.modal).show();
    },
    hide() {
      UIkit.modal(this.$refs.modal).hide();
    },
  },
};
</script>
<style lang="less" scoped>
@import url("../../assets/less/variables.less");

#progress-and-cancel-row {
  display: flex;
  flex-flow: row wrap;
  place-content: stretch flex-start;
  align-items: center;
  width: 100%;
  flex: 0 0 auto;
}

#progress-and-cancel-row .stretchy {
  flex-grow: 1;
  margin-left: 5px;
  margin-right: 5px;
}

#progress-and-cancel-row .not-stretchy {
  flex-grow: 0;
  margin-left: 5px;
  margin-right: 5px;
}

#status-modal h2 {
  flex: 0 0 auto; // title never shrinks
}
</style>
