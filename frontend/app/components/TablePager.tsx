import { useEffect, useState } from "react";
import { ChevronLeft, ChevronRight, RefreshCw, Search, X } from "lucide-react";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "./ui/select";

export type PageSizeOption = 20 | 50 | 100;

type TablePagerProps = {
  keyword: string;
  onKeywordChange: (value: string) => void;
  offset: number;
  total: number;
  pageSize: PageSizeOption;
  onOffsetChange: (value: number) => void;
  onPageSizeChange: (value: PageSizeOption) => void;
  onRefresh?: () => void;
  disabled?: boolean;
  placeholder: string;
  className?: string;
};

const PAGE_SIZE_OPTIONS: PageSizeOption[] = [20, 50, 100];

/**
 * 后台表格通用分页条。
 *
 * @param keyword 当前模糊搜索关键字。
 * @param onKeywordChange 关键字变化回调。
 * @param offset 当前分页偏移量。
 * @param total 后端返回的总记录数。
 * @param pageSize 当前每页条数。
 * @param onOffsetChange 页码变化回调，传入新的 offset。
 * @param onPageSizeChange 每页条数变化回调。
 * @param onRefresh 刷新当前列表的回调。
 * @param disabled 是否禁用分页操作。
 * @param placeholder 搜索框占位文案。
 * @param className 额外样式类名。
 * @returns 表格顶部筛选与分页控制区域。
 */
export function TablePager({
  keyword,
  onKeywordChange,
  offset,
  total,
  pageSize,
  onOffsetChange,
  onPageSizeChange,
  onRefresh,
  disabled = false,
  placeholder,
  className = ""
}: TablePagerProps) {
  const [draftKeyword, setDraftKeyword] = useState(keyword);
  const pageCount = Math.ceil(total / pageSize);
  const currentPage = total === 0 ? 1 : Math.floor(offset / pageSize) + 1;
  const canPrev = offset > 0;
  const canNext = offset + pageSize < total;

  useEffect(() => {
    setDraftKeyword(keyword);
  }, [keyword]);

  /**
   * 提交搜索条件并回到第一页，确保后端分页从匹配结果首页开始读取。
   */
  function submitSearch() {
    const nextKeyword = draftKeyword.trim();
    onKeywordChange(nextKeyword);
    onOffsetChange(0);
  }

  /**
   * 清空关键字后立即刷新列表，避免用户还需要再按一次回车。
   */
  function clearSearch() {
    setDraftKeyword("");
    onKeywordChange("");
    onOffsetChange(0);
  }

  /**
   * 页大小变化时必须重置到第一页，避免旧 offset 落到新分页边界之外。
   */
  function handlePageSizeChange(value: string) {
    const nextSize = Number(value) as PageSizeOption;
    onPageSizeChange(nextSize);
    onOffsetChange(0);
  }

  return (
    <div className={`table-pager ${className}`.trim()}>
      <div className="table-pager-filters">
        <label className="pager-search">
          <input
            value={draftKeyword}
            onChange={(event) => setDraftKeyword(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter") {
                event.preventDefault();
                submitSearch();
              }
            }}
            placeholder={placeholder}
            disabled={disabled}
          />
          {draftKeyword ? (
            <button
              type="button"
              className="pager-search-clear"
              onClick={clearSearch}
              disabled={disabled}
              title="清空搜索"
              aria-label="清空搜索"
            >
              <X size={13} aria-hidden="true" />
            </button>
          ) : null}
          <button
            type="button"
            className="pager-search-submit"
            onClick={submitSearch}
            disabled={disabled}
            title="搜索"
            aria-label="搜索"
          >
            <Search size={13} aria-hidden="true" />
          </button>
        </label>
      </div>

      <div className="table-pager-controls" aria-label="分页">
        <span className="pager-total">{total} 条记录</span>
        <div className="pager-stepper">
          <button
            type="button"
            className="pager-icon-button"
            onClick={() => onOffsetChange(Math.max(0, offset - pageSize))}
            disabled={disabled || !canPrev}
            title="上一页"
            aria-label="上一页"
          >
            <ChevronLeft size={14} aria-hidden="true" />
          </button>
          <span className="pager-page mono">
            {currentPage}/{pageCount}
          </span>
          <button
            type="button"
            className="pager-icon-button"
            onClick={() => onOffsetChange(offset + pageSize)}
            disabled={disabled || !canNext}
            title="下一页"
            aria-label="下一页"
          >
            <ChevronRight size={14} aria-hidden="true" />
          </button>
        </div>
        <Select value={String(pageSize)} onValueChange={handlePageSizeChange} disabled={disabled}>
          <SelectTrigger className="pager-size-select" aria-label="每页条数">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {PAGE_SIZE_OPTIONS.map((option) => (
              <SelectItem key={option} value={String(option)}>
                {option}条/页
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        {onRefresh ? (
          <button
            type="button"
            className="pager-refresh"
            onClick={onRefresh}
            disabled={disabled}
            title="刷新"
            aria-label="刷新"
          >
            <RefreshCw size={14} aria-hidden="true" />
          </button>
        ) : null}
      </div>
    </div>
  );
}
