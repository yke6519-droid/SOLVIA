import paginationConfig from '@shared/pagination.json'

export const HISTORY_PAGE_SIZE = Number(paginationConfig.history_page_size) || 4
export const SESSION_PAGE_SIZE = Number(paginationConfig.session_page_size) || 20
