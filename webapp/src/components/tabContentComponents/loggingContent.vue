<template>
  <div ref="loggingDisplay" class="uk-padding uk-padding-remove-top logging-content">
    <!-- Logging nav bar -->
    <nav class="logging-navbar uk-navbar-container uk-navbar-transparent" uk-navbar="mode: click">
      <!-- Left side controls -->
      <div class="uk-navbar-left uk-padding-remove-top uk-padding-remove-bottom">
        <select v-model="filterLevel" class="uk-select">
          <option v-for="level in allLevels" :key="level">{{ level }}</option>
        </select>
      </div>

      <!-- Right side buttons -->
      <div class="uk-navbar-right">
        <div class="uk-grid">
          <div>
            <button
              class="uk-button uk-button-default uk-width-1-1"
              type="button"
              data-test-id="update-btn"
              @click="updateLogs()"
            >
              Refresh Logs
            </button>
          </div>
          <div>
            <EndpointButton
              class="uk-button uk-width-1-1"
              :url="logFileURI"
              button-label="Download Log File"
              :button-primary="false"
              data-test-id="download-btn"
            />
          </div>
        </div>
      </div>
    </nav>

    <!-- Logging items -->
    <div class="uk-align-center" style="width: 90%">
      <div v-if="filteredGroups.length === 0" class="logging-entry logging-entry-empty" uk-alert>
        <div class="logging-entry-body">
          <p v-if="groups.length === 0">No log entries have been recorded yet.</p>
          <p v-else>
            No log entries found at the current filter level (<strong>{{ filterLevel }}</strong> or
            higher) — {{ groups.length }} group{{ groups.length === 1 ? "" : "s" }} hidden below
            this level.
          </p>
          <p v-if="groups.length > 0">
            You can include lower levels using the dropdown in the top left.
          </p>
        </div>
      </div>

      <!-- One card per log group - pagedGroups are filtered groups on this page -->
      <LogCard
        v-for="group in pagedGroups"
        :key="group.key"
        :group="group"
        @toggle="toggleGroup(group)"
      />
    </div>

    <!-- Hidden entirely when there are no groups at all, rather than showing an empty pager -->
    <PaginateLinks
      v-if="filteredGroups.length > 0"
      :total-pages="totalPages"
      :current-page="currentPage"
      @change-page="changePage"
    />
  </div>
</template>

<script>
import axios from "axios";
import PaginateLinks from "@/components/genericComponents/paginateLinks.vue";
import EndpointButton from "../labThingsComponents/endpointButton.vue";
import LogCard from "./loggingComponents/logCard.vue";
import { useIntersectionObserver } from "@vueuse/core";
import { useSettingsStore } from "@/stores/settings.js";
import { mapState } from "pinia";

export default {
  name: "LoggingContent",

  components: {
    PaginateLinks,
    EndpointButton,
    LogCard,
  },

  emits: ["scrollTop"],

  data: function () {
    return {
      logs: [],
      groups: [],
      currentPage: 1,
      maxitems: 10,
      allLevels: ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
      filterLevel: "INFO",
    };
  },

  computed: {
    ...mapState(useSettingsStore, ["baseUri"]),
    filteredGroups() {
      const cutoff = this.allLevels.indexOf(this.filterLevel);
      return this.groups.filter((g) => {
        const gLevelIndex = this.allLevels.indexOf(g.highestLevel);
        return gLevelIndex >= cutoff;
      });
    },
    logURI: function () {
      return `${this.baseUri}/log/`;
    },
    logFileURI: function () {
      return `${this.baseUri}/logfile/`;
    },
    pagedGroups: function () {
      const startIndex = (this.currentPage - 1) * this.maxitems;
      return this.filteredGroups.slice(startIndex, startIndex + this.maxitems);
    },
    totalPages: function () {
      return Math.ceil(this.filteredGroups.length / this.maxitems);
    },
  },

  watch: {
    filterLevel() {
      this.currentPage = 1;
    },
  },

  mounted() {
    useIntersectionObserver(
      this.$refs.loggingDisplay,
      ([{ isIntersecting }]) => {
        this.visibilityChanged(isIntersecting);
      },
      {
        threshold: 0.0,
      },
    );
  },

  methods: {
    toggleGroup(group) {
      group.expanded = !group.expanded;
    },
    visibilityChanged(isVisible) {
      if (isVisible) {
        this.updateLogs({ collapseAll: true });
      }
    },
    // Fetches the latest logs and rebuilds the group list.
    // collapseAll: whether groups should be collapsed, or left in
    // their current state
    async updateLogs({ collapseAll = false } = {}) {
      let logs = await axios.get(this.logURI);
      this.logs = Object.values(logs.data);
      this.groups = this.groupLogs(this.logs, { collapseAll });

      // Ensure current page doesn't exceed limits after a refresh
      if (this.currentPage > this.totalPages) {
        this.currentPage = Math.max(this.totalPages, 1);
      }
    },

    // Buckets the flat log list into groups based on invocationID and order.
    // Also summarises the groups with their level
    groupLogs(logs, { collapseAll = false } = {}) {
      // Keep which groups were open before refreshing, so refreshing to see a current
      // action update doesn't close it.
      const previouslyExpanded = collapseAll
        ? new Set()
        : new Set(this.groups.filter((group) => group.expanded).map((group) => group.key));

      // Parse oldest -> newest so the first log in a group gives us
      // a stable identity for that group.
      const chronologicalLogs = [...logs].reverse();

      const groups = [];
      let currentGroup = null;
      let currentInvocationId = undefined;

      for (const log of chronologicalLogs) {
        // Skip any picamera2 logs without an invocationID, as they split up actions
        // due to being in a different thread, and are more likely to confuse a user
        // than be helpful
        if (log.logger === "picamera2.picamera2" && !log.invocation_id) {
          continue;
        }
        const invocationId = log.invocation_id || null;
        // Start a new group whenever the invocation id changes (including no invocation id)
        if (!currentGroup || invocationId !== currentInvocationId) {
          // Logs without an invocation_id fall into their own one-off "gap"
          // group
          const key = invocationId
            ? `invocation:${invocationId}:${log.sequence}`
            : `gap:${log.sequence}`;

          currentGroup = {
            key,
            invocationId,
            context: log.calling_action,
            loggerCounts: {},
            firstTimestamp: log.timestamp,
            logs: [],
            counts: { DEBUG: 0, INFO: 0, WARNING: 0, ERROR: 0, CRITICAL: 0 },
            total: 0,
            highestLevelIndex: 0,
            highestLevel: "DEBUG",
            expanded: previouslyExpanded.has(key),
          };
          groups.push(currentGroup);
          currentInvocationId = invocationId;
        }

        currentGroup.logs.push(log);
        currentGroup.counts[log.level]++;
        currentGroup.total++;

        // Track the most severe level in this group
        const idx = this.allLevels.indexOf(log.level);
        if (idx > currentGroup.highestLevelIndex) {
          currentGroup.highestLevelIndex = idx;
          currentGroup.highestLevel = log.level;
        }
      }

      // Newest group first
      return groups.sort((a, b) => b.firstTimestamp.localeCompare(a.firstTimestamp));
    },

    // Ensure we can't exceed the current top page
    changePage(page) {
      if (page >= 1 && page <= this.totalPages) {
        this.currentPage = page;
      }
    },
  },
};
</script>

<style lang="less" scoped>
@import url("../../assets/less/variables.less");

// Top bar holding the level filter dropdown and the refresh/download buttons
.logging-navbar {
  border-width: 0 0 1px;
  border-style: solid;
  border-color: rgba(180, 180, 180, 0.25);
  margin-bottom: 30px;
  height: 80px;
}

.logging-entry-empty {
  border: 1px solid rgba(180, 180, 180, 0.25);
  border-radius: 6px;
  margin-bottom: 14px;
}
</style>
