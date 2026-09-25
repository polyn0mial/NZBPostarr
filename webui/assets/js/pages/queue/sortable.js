// Queue page: SortableJS wrapper: every drag-and-drop list on the queue page goes through here.
// A feature module: a Vue options fragment that pages/queue/index.js merges into the page.
import Sortable from "sortablejs";

export default {
  methods: {
    destroySortableInstance(instanceKey) {
      const instance = this[instanceKey];
      if (instance) {
        instance.destroy();
        this[instanceKey] = null;
      }
    },

    createSortableInstance(instanceKey, container, options) {
      this.destroySortableInstance(instanceKey);
      if (!container) return null;
      const instance = new Sortable(container, options);
      this[instanceKey] = instance;
      return instance;
    },
  },
};
