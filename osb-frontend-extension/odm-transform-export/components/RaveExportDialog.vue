<template>
  <SimpleFormDialog
    ref="formRef"
    :title="$t('OdmTransformExport.title')"
    :open="true"
    :action-label="$t('OdmTransformExport.export')"
    :no-saving="report !== null"
    :cancel-label="report ? $t('_global.close') : null"
    max-width="900px"
    @close="emit('close')"
    @submit="exportForRave"
  >
    <template #body>
      <template v-if="!report">
        <p class="mb-6">
          {{
            $t('OdmTransformExport.description', {
              count: context.targets.length,
            })
          }}
        </p>
        <v-form ref="observer">
          <v-text-field
            v-model="raveStudyOid"
            :label="$t('OdmTransformExport.study_oid')"
            :hint="$t('OdmTransformExport.study_oid_hint')"
            persistent-hint
            class="mb-4"
            data-cy="rave-study-oid"
          />
          <v-text-field
            v-model="raveProtocolName"
            :label="$t('OdmTransformExport.protocol_name')"
            :hint="$t('OdmTransformExport.protocol_name_hint')"
            :rules="[(value) => formRules.max(value, 50)]"
            counter="50"
            persistent-hint
            data-cy="rave-protocol-name"
          />
        </v-form>
      </template>
      <template v-else>
        <v-alert type="success" variant="tonal" class="mb-4">
          {{
            $t('OdmTransformExport.downloaded', {
              filename: report.filename,
              count: report.applied_count,
            })
          }}
        </v-alert>
        <template v-if="skippedChanges.length > 0">
          <v-alert type="warning" variant="tonal" class="mb-4">
            {{
              $t('OdmTransformExport.manual_review', {
                count: skippedChanges.length,
              })
            }}
          </v-alert>
          <ChangesTable :changes="skippedChanges" />
        </template>
        <v-expansion-panels v-if="appliedChanges.length > 0" class="mt-4">
          <v-expansion-panel
            :title="
              $t('OdmTransformExport.applied_changes', {
                count: appliedChanges.length,
              })
            "
          >
            <v-expansion-panel-text>
              <ChangesTable :changes="appliedChanges" />
            </v-expansion-panel-text>
          </v-expansion-panel>
        </v-expansion-panels>
      </template>
    </template>
  </SimpleFormDialog>
</template>

<script setup>
import { computed, inject, ref } from 'vue'
import SimpleFormDialog from '@/components/tools/SimpleFormDialog.vue'
import exportLoader from '@/utils/exportLoader'
import ChangesTable from './ChangesTable.vue'
import odmTransformExportApi from '../api/extensions'

const props = defineProps({
  context: {
    type: Object,
    required: true,
  },
})
const emit = defineEmits(['close'])

const formRules = inject('formRules')

const formRef = ref(null)
const raveStudyOid = ref('')
const raveProtocolName = ref('')
const report = ref(null)

const skippedChanges = computed(
  () => report.value?.changes.filter((c) => c.status === 'skipped') ?? []
)
const appliedChanges = computed(
  () => report.value?.changes.filter((c) => c.status === 'applied') ?? []
)

async function exportForRave() {
  try {
    const resp = await odmTransformExportApi.getRaveExport({
      targetType: props.context.targetType,
      targets: props.context.targets,
      raveStudyOid: raveStudyOid.value.trim(),
      raveProtocolName: raveProtocolName.value.trim(),
    })
    exportLoader.downloadFile(resp.data.xml, 'text/xml', resp.data.filename)
    report.value = resp.data
  } finally {
    // API errors are already shown by the repository interceptor
    formRef.value.working = false
  }
}
</script>
