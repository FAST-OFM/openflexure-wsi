<template>
  <div ref="galleryDisplay" class="gallery-display">
    <!-- Gallery nav bar -->
    <nav class="ofm-tab-navbar">
      <!-- Right side buttons -->
      <div class="uk-navbar-right">
        <mini-menu v-slot="{ close }" icon="mobile_layout" :with-button="true">
          <p>
            <icon-button
              icon="tile_small"
              :show-label="true"
              label="Detailed cards"
              @click="
                thumbnailOnly = false;
                close();
              "
            />
          </p>
          <p>
            <icon-button
              icon="view_compact"
              :show-label="true"
              label="Thumbnail only"
              @click="
                thumbnailOnly = true;
                close();
              "
            />
          </p>
        </mini-menu>
        <button
          class="uk-button uk-button-default ofm-top-nav-button"
          type="button"
          @click="refreshGallery()"
        >
          Refresh
        </button>

        <button
          class="ofm-top-nav-square-button uk-button uk-button-default"
          type="button"
          @click="sidebarOpen = !sidebarOpen"
        >
          <div class="sidebar-toggle-text" :class="{ open: sidebarOpen }">❮</div>
        </button>
      </div>
    </nav>

    <gallery-modal ref="viewerModal" :selected-item="selectedItem" :base-uri="baseUri" />

    <!-- Gallery -->
    <div
      v-if="ready"
      class="ofm-container-with-sidebar"
      :class="{ 'sidebar-open': sidebarOpen }"
      uk-lightbox="toggle: .lightbox-link"
    >
      <!-- Gallery capture cards -->
      <main class="gallery-main">
        <div class="gallery-grid uk-grid-match" uk-grid>
          <div v-if="noItems">
            <h2>Nothing to show</h2>
            <p>There is no captured data to show.</p>
          </div>
          <div v-for="itemData in paginatedItems" :key="itemData.id">
            <gallery-card
              :item-data="itemData"
              :thumbnail-only="thumbnailOnly"
              @viewer-requested="showItem"
              @update-requested="refreshGallery"
            />
          </div>
        </div>
        <PaginateLinks
          :total-pages="totalPages"
          :current-page="currentPage"
          @change-page="changePage"
        />
      </main>
      <aside id="sidebar" class="ofm-sidebar" :class="{ open: sidebarOpen }">
        <div class="gallery-button">
          <multi-select-dropdown
            v-model="selectedCardTypes"
            :options="allCardTypes"
            title="Filter Gallery"
          />
        </div>
        <div
          v-for="bulkAction in bulkActions"
          :key="'action' + bulkAction.thing + bulkAction.action"
          class="gallery-button"
        >
          <server-specified-action-button
            :action-data="bulkAction"
            @error="modalError"
            @finished="onActionFinished(bulkAction)"
            @action-modal-closed="onActionModalClosed(bulkAction)"
          />
        </div>
        <div class="gallery-button">
          <action-button
            class="uk-width-1-1"
            thing="gallery"
            action="delete_all_data"
            submit-label="Delete All"
            :submit-data="{ card_types: selectedCardTypes }"
            :is-disabled="totalPages == 0"
            :can-terminate="true"
            :button-primary="false"
            :modal-progress="true"
            :requires-confirmation="true"
            :confirmation-message="deleteAllConfirmationMessage"
            @error="modalError"
            @action-modal-closed="refreshGallery"
          />
        </div>
      </aside>
    </div>
  </div>
</template>

<script>
import PaginateLinks from "@/components/genericComponents/paginateLinks.vue";
import MultiSelectDropdown from "@/components/genericComponents/multiSelectDropdown.vue";
import actionButton from "@/components/labThingsComponents/actionButton.vue";
import ServerSpecifiedActionButton from "@/components/labThingsComponents/serverSpecifiedActionButton.vue";
import MiniMenu from "@/components/genericComponents/miniMenu.vue";
import iconButton from "@/components/genericComponents/iconButton.vue";
import galleryCard from "./galleryComponents/galleryCard.vue";
import galleryModal from "./galleryComponents/galleryViewer.vue";
import { useIntersectionObserver } from "@vueuse/core";
import { useSettingsStore } from "@/stores/settings.js";
import { mapState } from "pinia";

// Export main app
export default {
  name: "GalleryContent",
  components: {
    actionButton,
    galleryCard,
    galleryModal,
    PaginateLinks,
    MultiSelectDropdown,
    ServerSpecifiedActionButton,
    MiniMenu,
    iconButton,
  },

  emits: ["scrollTop"],

  data: function () {
    return {
      all_items: [],
      selectedItem: null,
      osdViewer: null,
      currentPage: 1,
      selectedCardTypes: [],
      allCardTypes: [],
      bulkActions: [],
      sidebarOpen: false,
      thumbnailOnly: false,
    };
  },

  computed: {
    ...mapState(useSettingsStore, ["baseUri", "ready"]),
    filtered_items() {
      return this.all_items.filter((item) => this.selectedCardTypes.includes(item.card_type));
    },
    noItems() {
      return !this.filtered_items || this.filtered_items?.length === 0;
    },
    itemsPerPage() {
      return this.thumbnailOnly ? 25 : 18;
    },
    totalPages() {
      return Math.ceil((this.filtered_items?.length || 0) / this.itemsPerPage);
    },
    paginatedItems() {
      const start = (this.currentPage - 1) * this.itemsPerPage;
      return (this.filtered_items || []).slice(start, start + this.itemsPerPage);
    },
    deleteAllConfirmationMessage() {
      return `
        <p>Are you sure you want to delete all gallery data with the following types</p>
        <ul>
          ${this.selectedCardTypes.map((type) => `<li>${type}</li>`).join("\n")}
        </ul>
        <p>from the microscope?</p>
        <p>This is <b>irreversible</b>!</p>
      `;
    },
  },

  watch: {
    totalPages(newPageCount) {
      if (this.currentPage > newPageCount) {
        this.currentPage = Math.max(1, newPageCount);
      } else if (this.currentPage < 1) {
        this.currentPage == 1;
      }
    },
  },

  async mounted() {
    useIntersectionObserver(
      this.$refs.galleryDisplay,
      ([{ isIntersecting }]) => {
        this.visibilityChanged(isIntersecting);
      },
      {
        threshold: 0.0, // Adjust as needed
      },
    );
    this.allCardTypes = await this.readThingProperty("gallery", "card_types");
    this.selectedCardTypes = this.allCardTypes;
    this.bulkActions = await this.readThingProperty("gallery", "bulk_actions");
    // Update on mount (does nothing if not connected)
    await this.refreshGallery();
  },

  beforeUnmount() {
    // Then we call that function here to unwatch
    if (this.unwatchStoreFunction) {
      this.unwatchStoreFunction();
      this.unwatchStoreFunction = null;
    }
  },

  methods: {
    visibilityChanged(isVisible) {
      if (isVisible) {
        this.refreshGallery();
      }
    },
    async refreshGallery() {
      try {
        let all_items = await this.readThingProperty("gallery", "list_data");
        // if all_items is "falsey" or the number of items in all_items is zero then
        // all_items is equal to empty list.
        // stop refresh gallery as all_items is empty list.
        if (!all_items || all_items?.length === 0) {
          this.all_items = [];
          return;
        }
        all_items.sort((a, b) => {
          return b.created - a.created;
        });
        this.all_items = all_items;
      } catch (err) {
        console.error("Failed to refresh gallery items.");
        console.error(err);
        this.all_items = [];
      }
    },
    showItem(itemData) {
      if (itemData.card_type === "Scan") {
        if (itemData.gallery_info.dzi) {
          this.selectedItem = itemData;
          this.$refs.viewerModal.show();
        } else {
          this.modalError("Scan not stitched for viewing in webapp, please download or stitch");
        }
      } else {
        this.selectedItem = itemData;
        this.$refs.viewerModal.show();
      }
    },
    changePage(page) {
      if (page >= 1 && page <= this.totalPages && this.currentPage != page) {
        this.$emit("scrollTop");
        this.currentPage = page;
      }
    },
    onActionFinished(action) {
      // No modal will appear, so refresh immediately.
      // It is important not to refresh if there is a modal open, as the modal may
      // lose the context of the component that created it, making it hard to close.
      if (!action.modal_progress) {
        this.refreshGallery();
      }
    },
    onActionModalClosed(action) {
      // Only refresh after the modal closes if there was one.
      if (action.modal_progress) {
        this.refreshGallery();
      }
    },
  },
};
</script>

<style lang="less" scoped>
.gallery-display {
  height: 100%;
  overflow-y: hidden;
}

.gallery-main {
  height: calc(100% - var(--ofm-navbar-height));
  box-sizing: border-box;
  overflow-y: auto;
  padding-left: 20px;
  padding-top: 20px;
}

.sidebar-toggle-text {
  font-size: 24px;
  transition: transform 0.25s ease;
}

.sidebar-toggle-text.open {
  transform: rotate(180deg);
}
</style>
