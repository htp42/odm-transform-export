import { i18n } from '@/plugins/i18n'

export default [
  {
    title: i18n.t('OdmTransformExport.menu_label'),
    icon: 'mdi-file-export-outline',
    component: () => import('../components/RaveExportDialog.vue'),
  },
]
