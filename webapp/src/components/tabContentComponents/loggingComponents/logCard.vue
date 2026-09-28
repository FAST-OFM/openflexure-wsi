<template>
  <div class="logging-entry">
    <!-- Clicking anywhere in the header toggles expand/collapse -->
    <div class="logging-header" @click="$emit('toggle')">
      <span class="log-title">{{ groupTitle }}: </span>
      <span class="log-stats"> {{ group.total }} message{{ group.total === 1 ? "" : "s" }}</span>

      <!-- Error/warning/info counts, each with a "|" divider only shown when there's something to divide -->
      <span class="log-stats">
        <span v-if="errorCount > 0" class="log-divider">|</span>
        <span v-if="errorCount > 0" class="log-stat log-stat-error">
          <span class="material-symbols-outlined log-stat-icon">cancel</span>
          {{ errorCount }} errors
        </span>

        <span v-if="group.counts.WARNING > 0" class="log-divider">|</span>
        <span v-if="group.counts.WARNING > 0" class="log-stat log-stat-warning">
          <span class="material-symbols-outlined log-stat-icon">warning</span>
          {{ group.counts.WARNING || 0 }} warnings
        </span>

        <!-- Info count always shows (no v-if), so it always gets its divider too -->
        <span class="log-divider">|</span>
        <span class="log-stat log-stat-info">
          <span class="material-symbols-outlined log-stat-icon">info</span>
          {{ infoCount }} info
        </span>
      </span>

      <!-- Separate expand/collapse control. Use .stop so it doesn't also change from the header's click handler -->
      <a class="more-info" @click.stop="$emit('toggle')">
        {{ group.expanded ? "Collapse" : "Expand" }}
        <span
          class="material-symbols-outlined chevron-icon"
          :class="{ 'chevron-open': group.expanded }"
        >
          expand_more
        </span>
      </a>
    </div>

    <!-- Only rendered when expanded, so collapsed groups don't take time rendering -->
    <div v-if="group.expanded" class="logging-body">
      <!-- Sorted so newest log line in the group shows first -->
      <div v-for="item in sortedLogs" :key="item.sequence" class="logging-message-row">
        <span class="log-date">{{ formatGroupTime(item.timestamp) }}</span>
        <span class="log-level" :class="levelClass(item.level)">{{ item.level }}</span>
        <span class="logging-message-text">{{ item.message }}</span>
      </div>
    </div>
  </div>
</template>

<script>
import { formatKey } from "@/js_utils/formatter.mjs";

export default {
  name: "LogCard",

  props: {
    // A single log group, as built by loggingContent's groupLogs():
    // { key, invocationId, context, firstTimestamp, logs, counts, total,
    //   highestLevelIndex, highestLevel, expanded }
    group: {
      type: Object,
      required: true,
    },
  },

  emits: ["toggle"],

  computed: {
    // CRITICAL are counted as ERROR for summary
    errorCount() {
      return (this.group.counts.ERROR || 0) + (this.group.counts.CRITICAL || 0);
    },
    // DEBUG counted as INFO
    infoCount() {
      return (this.group.counts.INFO || 0) + (this.group.counts.DEBUG || 0);
    },
    // Get the group title as the last part of the logger supplying the message
    groupTitle() {
      if (!this.group.context || !this.group.invocationId) return "Device";
      const parts = this.group.context.split(".");
      return formatKey(parts[parts.length - 1]);
    },
    /*
     * Sort logs within a group with the earliest first.
     */
    sortedLogs() {
      return [...this.group.logs].sort((b, a) => b.timestamp.localeCompare(a.timestamp));
    },
  },

  methods: {
    levelClass(level) {
      switch (level) {
        case "CRITICAL":
        case "ERROR":
          return "log-level-error";
        case "WARNING":
          return "log-level-warning";
        default:
          return "log-level-info";
      }
    },
    // Convert from Python timestamps to ones understood by Vue
    formatGroupTime(ts) {
      const d = new Date(ts.replace(",", "."));
      return d.toLocaleString();
    },
  },
};
</script>

<style lang="less" scoped>
@import url("../../../assets/less/variables.less");
// Pinched from ui-kit
@error-color: #f0506e;
@warning-color: #faa05a;
@info-color: @global-primary-background;

// Card wrapper for a single log group (one invocation, or one group of root logs)
.logging-entry {
  border: 1px solid rgba(180, 180, 180, 0.25);
  border-radius: 6px;
  margin-bottom: 14px;
}

// Clickable strip at the top of each group: title, counts, expand/collapse control
.logging-header {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 14px 20px;
  flex-wrap: wrap;
  cursor: pointer;
  background-color: rgba(180, 180, 180, 0.2);
}

// Group name (derived from the logger path), shown on the left of the header
.log-title {
  font-weight: 700;
  font-size: 1.05rem;
}

// Row of error/warning/info counts within the header
.log-stats {
  display: flex;
  align-items: center;
  gap: 10px;
  font-weight: 650;
}

// "|" separators placed between the stat pills
.log-divider {
  color: @global-muted-color;
}

// Single stat pill (icon + count), e.g. "3 errors"
.log-stat {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  white-space: nowrap;
}

.log-stat-icon {
  font-size: 1.25rem;
  display: inline-block;
  vertical-align: middle;
}

// Expand/collapse chevron next to the "Expand"/"Collapse" link
.chevron-icon {
  font-size: 1.2rem;
  display: inline-block;
  transition: transform 0.15s ease;
  vertical-align: middle;
}

// Flips the chevron upside-down when its group is expanded
.chevron-open {
  transform: rotate(180deg);
}

// Colour coding for the three stat pills, reusing UIkit's danger/warning palette
.log-stat-error {
  color: @error-color;
}

.log-stat-warning {
  color: @warning-color;
}

.log-stat-info {
  color: @info-color;
}

// "Expand"/"Collapse" text link, pinned to the right edge of the header
.more-info {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  text-decoration: none;
  font-weight: 600;
  margin-left: auto;
  cursor: pointer;
  color: inherit;
}

// Scrollable list of individual log lines shown when a group is expanded
.logging-body {
  padding: 0 16px 16px;
  display: flex;
  flex-direction: column;
  background-color: rgba(180, 180, 180, 0.1);
  // Caps each expanded group at ~70% of the viewport height so a single
  // large group can always be collapsed
  max-height: 70vh;
  overflow-y: auto;
}

// One line of the expanded log list: timestamp, level badge, message
.logging-message-row {
  display: flex;
  align-items: baseline;
  gap: 12px;
  padding: 7px 0;
  border-bottom: 1px solid rgba(180, 180, 180, 0.25);
  font-family: monospace;
  font-size: 0.9rem;
}

// Fixed-width severity badge (INFO/WARNING/ERROR) on each row
.log-level {
  font-size: 0.8rem;
  font-weight: 600;
  letter-spacing: 0.03em;
  flex-shrink: 0;
  white-space: nowrap;
  width: 60px;
}

// Timestamp column; prevented from shrinking so long messages don't squeeze it
.log-date {
  flex-shrink: 0;
}

// Colour coding for individual log rows, matching the header stat colours
.log-level-error {
  color: @error-color;
}

.log-level-warning {
  color: @warning-color;
}

.log-level-info {
  color: @info-color;
}

// Message text: wraps on any character so long unbroken strings (e.g. paths,
// stack traces) don't overflow the row
.logging-message-text {
  white-space: break-spaces;
  overflow-wrap: anywhere;
}
</style>
