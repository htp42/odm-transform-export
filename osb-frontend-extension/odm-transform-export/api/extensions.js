import repository from '@/api/repositoryExtensions'

export default {
  /**
   * Export ODM targets as Medidata Rave-compatible ODM XML.
   *
   * @param {Object}   options
   * @param {string}   options.targetType         - ODM target type, e.g. 'form'
   * @param {string[]} options.targets            - Target UIDs
   * @param {string}   [options.raveStudyOid]     - Study OID override
   * @param {string}   [options.raveProtocolName] - ProtocolName override, 50 characters max
   * @returns {Promise} Resolves to { filename, xml, applied_count, skipped_count, changes }
   */
  getRaveExport({ targetType, targets, raveStudyOid, raveProtocolName }) {
    // URLSearchParams repeats `targets`, the list format FastAPI expects
    const params = new URLSearchParams({ target_type: targetType })
    for (const target of targets) {
      params.append('targets', target)
    }
    if (raveStudyOid) {
      params.append('rave_study_oid', raveStudyOid)
    }
    if (raveProtocolName) {
      params.append('rave_protocol_name', raveProtocolName)
    }
    return repository.get('/odm-transform-export-ext/rave-exports', { params })
  },
}
