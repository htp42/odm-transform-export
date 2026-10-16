<template>
  <v-table density="compact" class="text-body-small">
    <thead>
      <tr>
        <th>{{ $t('OdmTransformExport.rule') }}</th>
        <th>{{ $t('OdmTransformExport.location') }}</th>
        <th>{{ $t('OdmTransformExport.issue') }}</th>
      </tr>
    </thead>
    <tbody>
      <tr v-for="(change, index) in changes" :key="index">
        <td class="text-no-wrap">{{ change.rule }}</td>
        <td class="location">{{ change.location }}</td>
        <td>
          {{ change.description }}
          <div v-if="change.note" class="text-medium-emphasis">
            {{ change.note }}
          </div>
        </td>
      </tr>
    </tbody>
  </v-table>
</template>

<script setup>
defineProps({
  changes: {
    type: Array,
    required: true,
  },
})
</script>

<style scoped>
td {
  vertical-align: top;
}
.location {
  word-break: break-all;
  font-family: monospace;
}
</style>
