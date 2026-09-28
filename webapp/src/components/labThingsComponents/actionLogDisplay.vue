<template>
  <div class="action-log-display">
    <!-- Toggle button -->
    <button
      v-show="log.length"
      class="log-toggle"
      :title="isCollapsed ? 'Show full log' : 'Hide full log'"
      :aria-label="isCollapsed ? 'Show full log' : 'Hide full log'"
      @click="toggleLog"
    >
      <span class="material-symbols-outlined" aria-hidden="true">
        {{ isCollapsed ? "expand_more" : "expand_less" }}
      </span>
    </button>

    <!-- Header always shows latest message -->
    <div class="log-summary">
      <div class="log-summary-text">
        <div v-if="taskStatus === 'error'" class="status-error">
          <p>The task failed due to an error:<br />{{ errorMessage }}</p>
        </div>

        <div v-else-if="taskStatus == 'cancelled'" class="status-cancelled">
          The task was cancelled.
        </div>

        <div v-else-if="taskStatus == 'completed'" class="status-completed">
          The task completed successfully.
        </div>

        <div v-else-if="latestMessage">
          {{ latestMessage }}
        </div>
      </div>
    </div>

    <!-- Expanded view shows log -->
    <transition name="log-fade">
      <div
        v-if="!isCollapsed"
        class="log-wrapper"
        @mouseenter="onMouseEnter"
        @mouseleave="onMouseLeave"
      >
        <div ref="logContainer" class="log-container uk-margin-left uk-margin-right uk-margin">
          <div v-if="log">
            <div v-for="(item, index) in log" :key="`log_entry_${index}`">
              {{ item.message }}
            </div>
          </div>
          <div v-if="taskStatus === 'error'" class="uk-alert uk-alert-danger">
            <p><b>The task failed due to an error:</b></p>
            <p>{{ errorMessage }}</p>
          </div>
          <div v-if="taskStatus == 'cancelled'" class="uk-alert uk-alert-warning">
            The task was cancelled.
          </div>
          <div v-if="taskStatus == 'completed'" class="uk-alert uk-alert-success">
            The task completed successfully.
          </div>
        </div>

        <!-- Paused banner outside scroll container, positioned relative to wrapper -->
        <div v-if="userIsHovering" class="paused-banner">Auto-scroll paused</div>
      </div>
    </transition>
  </div>
</template>

<script>
export default {
  name: "ActionLogDisplay",

  props: {
    taskStatus: {
      type: String,
      required: true,
    },
    log: {
      type: Array,
      required: true,
    },
    collapsedByDefault: {
      type: Boolean,
      default: true,
    },
  },

  data() {
    return {
      userIsHovering: false,
      isCollapsed: this.collapsedByDefault,
    };
  },

  computed: {
    latestMessage() {
      if (!this.log.length) {
        return "";
      }

      return this.log[this.log.length - 1].message;
    },

    errorMessage() {
      let logLength = this.log.length;
      var defaultMessage = "Unexpected error, please check the logs";
      if (logLength == 0) {
        return defaultMessage;
      }
      // Cannot use .at until we update OpenFlexure Connect
      if (this.log[logLength - 1].levelname != "ERROR") {
        return defaultMessage;
      }
      return this.log[logLength - 1].message || defaultMessage;
    },
  },

  watch: {
    log: {
      handler() {
        if (!this.isCollapsed) {
          this.scrollToBottom();
        }
      },
      deep: true,
    },

    taskStatus() {
      if (!this.isCollapsed) {
        this.scrollToBottom();
      }
    },

    collapsedByDefault(newValue) {
      this.isCollapsed = newValue;
    },
  },

  methods: {
    /**
     * Toggles the log visibility and scrolls to the bottom if opened.
     * @public
     */
    toggleLog() {
      this.isCollapsed = !this.isCollapsed;

      if (!this.isCollapsed) {
        this.scrollToBottom();
      }
    },

    /**
     * Sets the userIsHovering state to true when the mouse enters the log area.
     * @public
     */
    onMouseEnter() {
      this.userIsHovering = true;
    },
    /**
     * Sets the userIsHovering state to false when the mouse leaves the log area.
     * @public
     */
    onMouseLeave() {
      this.userIsHovering = false;
    },
    /**
     * Scrolls the log container to the bottom unless the user is hovering over it.
     * @public
     */
    scrollToBottom() {
      /*Scroll to bottom of log unless the user is hovering over the log.*/
      this.$nextTick(() => {
        if (this.userIsHovering) return;

        const viewer = this.$refs.logContainer;
        viewer.scrollTop = viewer.scrollHeight;
      });
    },
  },
};
</script>

<style scoped>
/* Expanded view */
.log-wrapper {
  position: relative;
  flex: 1 1 200px; /* prefer 200px, but shrink if the container runs out of room */
  min-height: 2.6em; /* hard floor: roughly two lines of text */
  overflow: hidden;
  margin-bottom: 0;
  margin-top: 0;
  display: flex;
  flex-direction: column;
}

.action-log-display {
  width: 100%;
  position: relative;
  flex: 1 1 auto;
  min-height: 0;
  display: flex;
  flex-direction: column;
}

.log-summary {
  padding: 2px;
  padding-right: 30px; /* reserve room for the toggle button, prevents overlap */
  display: block;
  align-items: center;
}

.log-summary-text {
  text-align: center;
  font-size: large;

  /* Reserve two lines even when message is short. line-clamp handles truncation */
  min-height: calc(2 * 1.4em);
  line-height: 1.4em;
  padding: 0 5px; /* Give some padding so there's room for ... to not expand too far */
  overflow: hidden;
  display: -webkit-box;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 2;
  line-clamp: 2; /* Future version of webkit-line-clamp, not fully supported yet */
}

.status-error {
  color: #f0506e;
  font-weight: 600;
}

.status-cancelled {
  color: #faa05a;
  font-weight: 600;
}

.status-completed {
  color: green;
  font-weight: 600;
}

.log-container {
  flex: 1 1 auto;
  min-height: 0;
  overflow: auto;
  background-color: white;
  color: black;
  padding: 0.5em;
  border: 1px solid black;
  box-sizing: border-box;
}

/* Floating banner fixed at top-right of wrapper, not affected by scroll */
.paused-banner {
  position: absolute;
  top: 8px;
  right: 40px;
  color: black;
  font-weight: bold;
  padding: 4px;
  border-radius: 4px;
  z-index: 10;
  font-size: 0.85em;
  pointer-events: none;
  box-shadow: 0 0 4px rgba(0, 0, 0, 0.2);
  background-color: white;
}

/* Toggle button */
.log-toggle {
  position: absolute;
  top: 0;
  right: 2px;
  background: transparent;
  border: none;
  padding: 4px;
  color: var(--uk-inverse-color, inherit); /* get the colour of the text from ui-kit */
}

.material-symbols-outlined {
  font-size: 28px;
  color: inherit; /* inherit from .log-toggle so it tracks the theme color */
}

/* Handles the entire animation, from either enter-from or leave-to */
.log-fade-enter-active,
.log-fade-leave-active {
  transition:
    max-height 0.25s ease,
    opacity 0.25s ease;
  overflow: hidden;
}

/* The collapsed part of the animation, whether at the start or end */
.log-fade-enter-from,
.log-fade-leave-to {
  max-height: 0;
  opacity: 0;
}

/* The expanded part of the animation, whether at the start or end */
.log-fade-enter-to,
.log-fade-leave-from {
  max-height: 200px;
  opacity: 1;
}
</style>
