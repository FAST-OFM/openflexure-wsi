<template>
  <div v-if="totalPages > 1" class="pagination-container uk-margin-top uk-flex uk-flex-center">
    <ul class="uk-pagination">
      <li :class="{ 'uk-disabled': currentPage === 1 }">
        <a href="#" @click.prevent="$emit('changePage', currentPage - 1)">
          <span uk-pagination-previous></span>
        </a>
      </li>

      <li v-for="page in totalPages" :key="page" :class="{ 'uk-active': page === currentPage }">
        <a href="#" @click.prevent="$emit('changePage', page)">{{ page }}</a>
      </li>

      <li :class="{ 'uk-disabled': currentPage === totalPages }">
        <a href="#" @click.prevent="$emit('changePage', currentPage + 1)">
          <span uk-pagination-next></span>
        </a>
      </li>
    </ul>
  </div>
</template>
<script>
// Export main app
export default {
  name: "PaginateLinks",

  props: {
    totalPages: {
      type: Number,
      required: true,
    },
    currentPage: {
      type: Number,
      required: true,
    },
  },

  emits: ["changePage"],
};
</script>
<style lang="less" scoped>
.pagination-container {
  text-align: center;
}
</style>
