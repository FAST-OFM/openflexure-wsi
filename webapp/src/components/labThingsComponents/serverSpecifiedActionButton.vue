<template>
  <action-button
    :thing="actionData.thing"
    :action="actionData.action"
    :poll-interval="actionData.poll_interval"
    :submit-label="actionData.submit_label"
    :can-terminate="actionData.can_terminate"
    :requires-confirmation="actionData.requires_confirmation"
    :confirmation-message="actionData.confirmation_message"
    :button-primary="actionData.button_primary"
    :is-disabled="actionData.disabled"
    :modal-progress="actionData.modal_progress"
    :stream-with-modal="actionData.stream_with_modal"
    :is-broken="actionData.broken"
    @task-started="actionStarted"
    @response="actionResponse"
    @error="modalError"
    @finished="actionFinished"
    @action-moded="actionModalClosed"
  />
</template>

<script>
import ActionButton from "./actionButton.vue";

// Export main app
export default {
  name: "ServerSpecifiedActionButton",

  components: {
    ActionButton,
  },

  props: {
    actionData: {
      type: Object,
      required: true,
    },
  },

  emits: ["started", "response", "finished", "requestUpdate", "actionModalClosed"],

  methods: {
    /**
     * Runs when the ActionButton's action completes successfully.
     *
     * It is used to send success notifications. It also forwards the response to the
     * parent.
     */
    actionResponse: function (response) {
      if (this.actionData.notify_on_success) {
        if (this.actionData.response_is_success_message) {
          this.modalNotify(response.output);
        } else {
          this.modalNotify(this.actionData.success_message);
        }
        if (this.actionData.update_interface_on_response) {
          this.$emit("requestUpdate");
        }
        this.$emit("response", response);
      }
    },
    /**
     * Runs when the ActionButton's action starts
     *
     * This forwards the event to the parent
     */
    actionStarted: function () {
      this.$emit("started");
    },
    /**
     * Runs when the ActionButton's action finishes in any way (error, cancel,
     * completion).
     *
     * This forwards the event to the parent
     */
    actionFinished: function () {
      this.$emit("finished");
    },
    actionModalClosed() {
      this.$emit("actionModalClosed");
    },
  },
};
</script>

<style lang="less"></style>
