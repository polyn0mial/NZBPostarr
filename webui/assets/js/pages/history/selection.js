// Row selection on the History page: jobs, flat items and group/season header checkboxes.
// selectionMethods is spread into the page's methods; the group helpers are pure.

// The items a group or season header checkbox acts on: its episodes when it has any (packs
// are ignored), otherwise every item.
export function groupSelectionTargets(group) {
    if (!group || !Array.isArray(group.items) || !group.items.length) return [];
    const eps = group.items.filter(it => it.episode_number != null);
    return eps.length > 0 ? eps : group.items;
}

// A header is checked when every one of its selection targets is selected.
export function isGroupFullySelected(group, selectedItems) {
    const targets = groupSelectionTargets(group);
    return targets.length > 0 && targets.every(it => selectedItems.has(it.item_name));
}

export const selectionMethods = {
    toggleSelectAllJobs() {
        if (this.isAllJobsSelected) {
            this.selectedJobIds = [];
        } else {
            this.selectedJobIds = this.jobsList.map(j => j.job_id);
        }
    },

    toggleSelectAll(checked) {
        if (checked) {
            this.uploads.forEach(item => this.selectedItems.add(item.item_name));
        } else {
            this.selectedItems.clear();
        }
        this.selectAll = checked;
    },

    toggleItemSelection(itemName, checked) {
        if (checked) {
            this.selectedItems.add(itemName);
        } else {
            this.selectedItems.delete(itemName);
        }
        // Update selectAll state
        this.selectAll = this.selectedItems.size === this.uploads.length && this.uploads.length > 0;
    },

    isItemSelected(itemName) {
        return this.selectedItems.has(itemName);
    },

    isGroupSelected(group) {
        return isGroupFullySelected(group, this.selectedItems);
    },

    toggleGroupSelection(group, checked) {
        const targets = groupSelectionTargets(group);
        if (!targets.length) return;

        targets.forEach(it => {
            if (checked) {
                this.selectedItems.add(it.item_name);
            } else {
                this.selectedItems.delete(it.item_name);
            }
        });

        // If we are unchecking, and it's a season/group header, also uncheck any packs in that group/season
        // because otherwise checking the season again would do nothing (behaviorally, usually uncheck = full clear)
        if (!checked) {
            group.items.forEach(it => this.selectedItems.delete(it.item_name));
        }

        this.selectAll = this.selectedItems.size === this.uploads.length && this.uploads.length > 0;
    },
};
