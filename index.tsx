import { CloseOutlined, PlusOutlined, ProfileOutlined } from '@ant-design/icons';
import {
  Button,
  Checkbox,
  DatePicker,
  Drawer,
  Form,
  Image,
  Input,
  InputNumber,
  message,
  Modal,
  Select,
  Space,
  Table,
  Tabs,
  Tag,
  Upload,
} from 'antd';
import type { TableProps, UploadFile, UploadProps } from 'antd';
import type { RcFile } from 'antd/es/upload';
import dayjs, { type Dayjs } from 'dayjs';
import { useEffect, useMemo, useRef, useState } from 'react';
import type { Key } from 'react';
import { useRequest } from 'ahooks';
import {
  getFactoryOwnCustomerInfosForSelectApi,
  getFactoryOwnCustomerInfosForSelectResData,
  getGoodBoxBindForecastItemsApi,
  getGoodBoxBindSaleSubItemInfosApi,
  goodBoxReviewApi,
} from '@/services/madeManageService/goodBox';
import {
  getGoodBoxCompanyInfosComboxApi,
  getGoodBoxCompanyInfosComboxResData,
  quickCreateGoodBoxApi,
} from '@/services/madeManageService/goodBoxList';
import MESTable from '@/components/MESUI/MESTable';
import {
  applyExcelTextFilters,
  useExcelTextFilters,
  type ExcelTextFilterFieldDef,
} from '@/components/MESUI/ExcelTextFilter';
import { MAX_IMAGE_COUNT, useGoodBoxImageUpload } from '../hooks/useGoodBoxImageUpload';
import styles from './index.less';

/** 拟装载明细行：销售子行 / 预测子行共用，按需读取字段（销售用 f_saleCount、预测用 f_orderCount） */
type SelectedSubItemRow = {
  f_subItemID?: number;
  f_forecastOrderID?: number;
  f_itemID?: number;
  f_itemLineNo?: number | string;
  f_demandTypeNo?: string | null;
  f_customerMaterialNo?: string;
  /** 客户料名 */
  f_customerMaterialName?: string;
  f_customerCode?: string;
  f_materialCode?: string;
  f_materialCode1?: string;
  f_materialCode2?: string;
  /** 物料名称（可分配列表展示） */
  f_materialName?: string;
  /** 交货日期（销售子行）/ 出货日期（预测子行） */
  f_deliverDate?: string;
  f_planShipDate?: string;
  /** 库存数量 / 计划状态，用于可分配列表展示 */
  f_storeCount?: number;
  f_planStatusName?: string;
  f_orderStatusTitle?: string;
  /** 单件重量 */
  f_weight?: number;
  f_saleCount?: number;
  f_saleUnitName?: string;
  f_orderCount?: number;
  f_unitName?: string;
  f_isCanSubmit?: number;
  f_submitFailReason?: string;
  /** 实际发货数量（每柜独立）：弹窗内可编辑、必填 */
  f_sendCount?: number | null;
  f_standardWeight?: number;
  f_sumWeight?: number;
  f_weightUnitName?: string;
};

/** 计划行：在原行基础上强制携带数量字段 —— 总表行=实际发货数，计划行=分配数量 */
type CabinetRow = SelectedSubItemRow & { f_sendCount: number };

/** 一个计划（计划总表 / 计划） */
type Cabinet = {
  /** 计划唯一 key（Tab key）；计划总表固定为 'first' */
  key: string;
  type: 'first' | 'sub';
  name: string;
  /** 计划计划日期（选填，预留）；计划总表不使用该字段，随表单顶部日期 */
  date?: Dayjs | null;
  rows: CabinetRow[];
};

const FIRST_CABINET_KEY = 'first';

/** 表格 Excel 式筛选项（需求分类 / 物料料号 / 参考1 / 参考2）：明细分表用 */
const DETAIL_FILTER_FIELDS: ExcelTextFilterFieldDef<SelectedSubItemRow>[] = [
  { key: 'f_demandTypeNo', field: 'f_demandTypeNo' },
  { key: 'f_materialCode', field: 'f_materialCode' },
  { key: 'f_materialName', field: 'f_materialName' },
  { key: 'f_materialCode1', field: 'f_materialCode1' },
  { key: 'f_materialCode2', field: 'f_materialCode2' },
];

/** 清单表行（未绑计划明细清单 / 待分配清单）：筛选只看 row 内的字段 */
type PoolTableRow = {
  row: SelectedSubItemRow;
  key?: string;
  total?: number;
  allocated?: number;
  remain?: number;
};

/** 表格 Excel 式筛选项：清单表用（字段嵌在 row 里） */
const POOL_FILTER_FIELDS: ExcelTextFilterFieldDef<PoolTableRow>[] = [
  { key: 'f_demandTypeNo', field: (item) => item.row.f_demandTypeNo },
  { key: 'f_materialName', field: (item) => item.row.f_materialName },
  { key: 'f_materialCode', field: (item) => item.row.f_materialCode },
  { key: 'f_materialCode1', field: (item) => item.row.f_materialCode1 },
  { key: 'f_materialCode2', field: (item) => item.row.f_materialCode2 },
];

/** 筛选字段 key：切换计划时统一清空，避免旧条件把新计划筛成空表 */
const FILTER_KEYS = [
  'f_demandTypeNo',
  'f_materialCode',
  'f_materialCode1',
  'f_materialCode2',
  'f_materialName',
] as const;


/** 明细行 rowKey：销售子行取 f_subItemID；预测子行退化为「预测单-行-行号」组合 */
const getDetailRowKey = (row: SelectedSubItemRow) =>
  String(
    row.f_subItemID ??
      `${row.f_forecastOrderID ?? ''}-${row.f_itemID ?? ''}-${row.f_itemLineNo ?? ''}`,
  );

/** 明细行销售数量：销售子行 f_saleCount / 预测子行 f_orderCount */
const getDetailRowCount = (row: SelectedSubItemRow) =>
  Number(row.f_saleCount ?? row.f_orderCount ?? 0) || 0;

/** 明细行单位 */
const getDetailRowUnit = (row: SelectedSubItemRow) =>
  String(row.f_saleUnitName ?? row.f_unitName ?? '').trim();

/**
 * 行总重：本柜发货数 × 理论重量（有理论重量时动态计算，改发货数即联动）；
 * 无理论重量时回退后端返回的 f_sumWeight，保持与货柜明细子表口径一致。
 */
const calcRowWeight = (row: CabinetRow): number => {
  const standard = Number(row.f_standardWeight);
  if (standard > 0) return (Number(row.f_sendCount) || 0) * standard;
  return Number(row.f_sumWeight) || 0;
};

const formatTotal = (value: number) => (Number.isInteger(value) ? String(value) : value.toFixed(2));

type CustomerOption = { value: number; label: string; disabled?: boolean };
type CustomerGroupOption = { label: string; title: string; options: CustomerOption[] };
type CompanyOption = { value: number; label: string };
type AddFormValues = {
  isForecastGoodBox?: number;
  customerID?: number;
  f_goodBoxDate?: Dayjs | null;
  f_goodBoxDescription?: string;
  ticketNo?: string;
  companyInit?: number;
};

const buildCustomerOptions = (
  groups: getFactoryOwnCustomerInfosForSelectResData[],
): (CustomerOption | CustomerGroupOption)[] =>
  (Array.isArray(groups) ? groups : []).flatMap(
    (group): (CustomerOption | CustomerGroupOption)[] => {
      const items: CustomerOption[] = (group.customerInfos || []).map((item) => ({
        value: item.customerID,
        label: item.customerCode,
        disabled: item.isDisabled === 1,
      }));
      const className = String(group.customerClassName ?? '').trim();
      return className ? [{ label: className, title: className, options: items }] : items;
    },
  );

const buildCompanyOptions = (
  groups: getGoodBoxCompanyInfosComboxResData[],
): (CompanyOption | CustomerGroupOption)[] =>
  (Array.isArray(groups) ? groups : []).flatMap(
    (group): (CompanyOption | CustomerGroupOption)[] => {
      const items: CompanyOption[] = (group.companyInfos || []).map((item) => ({
        value: item.companyInit,
        label: String(item.companyShortName || item.companyFullName || item.companyCode),
      }));
      const className = String(group.companyClassName ?? '').trim();
      return className ? [{ label: className, title: className, options: items }] : items;
    },
  );

/**
 * 新建发货计划抽屉（GoodBoxList 页专用）：
 * 一张单据下拆成「1 计划总表 + N 计划」，用 Tab 切换：
 * - 计划总表：从「未绑计划明细清单」加入子行并输入实际发货数（计划总发货量）；
 * - 计划：从「待分配清单」把总表子行分配进来，每行填分配数量；
 *   实际发货数 = 各计划分配总数（待分配为 0）时，该子行从待分配清单中消失；
 * - 每个 Tab 下方的清单随选中页签切换（总表 → 未绑计划明细清单；计划 → 待分配清单）；
 * - 保存时逐计划调用 ff_quickCreateGoodBox（计划总表携图片），生成多个单号。
 */
const AddSendPlanDrawer: React.FC<{
  addOpen: boolean;
  onCancel: () => void;
  onOk: (newGoodBoxNo?: string) => void;
  factoryInit: string;
  defaultIsForecast?: number;
  defaultCustomerID?: number;
  selectedRows?: unknown[];
  /** 页面未绑视图已请求到的明细子行（快照）：候选池直接复用，不再重复请求接口 */
  unboundRows?: unknown[];
}> = ({
  addOpen,
  onCancel,
  onOk,
  factoryInit,
  defaultIsForecast = 0,
  defaultCustomerID,
  selectedRows = [],
  unboundRows = [],
}) => {
  const [form] = Form.useForm();
  const [saveLoading, setSaveLoading] = useState(false);
  const [submitLoading, setSubmitLoading] = useState(false);

  const { fileList, setFileList, uploadingCount, addFile, addPastedFiles, getSaveImageNames } =
    useGoodBoxImageUpload(addOpen);
  const [previewOpen, setPreviewOpen] = useState(false);
  const [previewSrc, setPreviewSrc] = useState('');

  // 候选池：所有可选子行（页面勾选 + 抽屉补充），按 rowKey 全局去重
  const [allPool, setAllPool] = useState<SelectedSubItemRow[]>([]);
  // 柜体列表：首柜固定 + n 个子柜
  const [cabinets, setCabinets] = useState<Cabinet[]>([]);
  const [activeTabKey, setActiveTabKey] = useState<string>(FIRST_CABINET_KEY);
  const subSeqRef = useRef(0);
  // 分配超上限提示节流：连续输入时 onChange 会多次触发，1 秒内只提示一次
  const limitWarnRef = useRef(0);
  const [createdGoodBoxNos, setCreatedGoodBoxNos] = useState<string[]>([]);
  const [detailLoading, setDetailLoading] = useState(false);
  // 未绑计划明细清单 / 待分配清单勾选行（多选批量加入计划总表 / 本计划）
  const [unboundSelectedKeys, setUnboundSelectedKeys] = useState<Key[]>([]);
  const [pendingSelectedKeys, setPendingSelectedKeys] = useState<Key[]>([]);
  // 明细表高度（上下两张表之间可拖拽拉伸）
  const [detailTableHeight, setDetailTableHeight] = useState(240);
  const detailResizeRef = useRef<{
    startY: number;
    startHeight: number;
    maxHeight: number;
  } | null>(null);
  // 右侧明细面板实测高度：用于收敛明细表高度与拖拽上限，保证不溢出外层
  const cabinetPanelRef = useRef<HTMLDivElement | null>(null);
  const [cabinetPanelHeight, setCabinetPanelHeight] = useState(0);
  // 清单页签内容区（.ant-tabs-content-holder）实测高度：清单表体 y = 该高度 - 表头高度
  const poolPanelRef = useRef<HTMLDivElement | null>(null);
  const [poolHolderHeight, setPoolHolderHeight] = useState(0);
  const openedRef = useRef(false);

  useEffect(() => {
    if (!addOpen) return;
    const cabinetEl = cabinetPanelRef.current;
    if (!cabinetEl) return;
    const sync = () => setCabinetPanelHeight(Math.round(cabinetEl.clientHeight));
    sync();
    const observer = new ResizeObserver(sync);
    observer.observe(cabinetEl);
    return () => observer.disconnect();
  }, [addOpen]);

  // 测量清单页签内容区高度：只由 flex 布局决定，不受表格自身高度影响。
  // content-holder 是 poolPanel 的 flex 子节点，观察 poolPanel 即可（尺寸变化必然联动），
  // 切换页签/计划时 effect 依赖重建，重新查询新的 holder
  useEffect(() => {
    if (!addOpen) return;
    const panel = poolPanelRef.current;
    if (!panel) return;

    const sync = () => {
      const holder = panel.querySelector<HTMLElement>('.ant-tabs-content-holder');
      if (holder) setPoolHolderHeight(Math.round(holder.clientHeight));
    };

    const observer = new ResizeObserver(sync);
    sync();
    const rafId = requestAnimationFrame(sync);
    observer.observe(panel);
    return () => {
      cancelAnimationFrame(rafId);
      observer.disconnect();
    };
  }, [addOpen, activeTabKey]);

  // 面板变矮（窗口缩小）时自动收敛明细表高度，保证明细表完整显示且清单仍留出空间
  useEffect(() => {
    if (cabinetPanelHeight <= 0) return;
    // 明细面板高 -（明细表头+合计约 78）- 分隔条 18 - 清单面板最小 170
    const maxHeight = Math.max(120, cabinetPanelHeight - 266);
    setDetailTableHeight((prev) => Math.min(prev, maxHeight));
  }, [cabinetPanelHeight]);

  // 拖拽分隔条：按住上下移动，实时改明细表高度（清单高度反向联动）
  useEffect(() => {
    const handleMouseMove = (event: MouseEvent) => {
      const state = detailResizeRef.current;
      if (!state) return;
      const next = state.startHeight + (event.clientY - state.startY);
      setDetailTableHeight(Math.min(state.maxHeight, Math.max(120, next)));
    };
    const handleMouseUp = () => {
      if (!detailResizeRef.current) return;
      detailResizeRef.current = null;
      document.body.style.cursor = '';
      document.body.style.userSelect = '';
    };
    window.addEventListener('mousemove', handleMouseMove);
    window.addEventListener('mouseup', handleMouseUp);
    return () => {
      window.removeEventListener('mousemove', handleMouseMove);
      window.removeEventListener('mouseup', handleMouseUp);
    };
  }, []);

  /** 开始拖拽明细表与清单之间的分隔条 */
  const handleDetailResizeStart = (event: React.MouseEvent<HTMLDivElement>) => {
    event.preventDefault();
    // 上限：明细面板高度 -（明细表头+合计约 78）- 分隔条 18 - 清单面板最小 170
    const maxHeight =
      cabinetPanelHeight > 0
        ? Math.max(120, cabinetPanelHeight - 266)
        : Math.max(200, window.innerHeight - 460);
    detailResizeRef.current = {
      startY: event.clientY,
      startHeight: detailTableHeight,
      maxHeight,
    };
    document.body.style.cursor = 'row-resize';
    document.body.style.userSelect = 'none';
  };

  const isForecast = Number(defaultIsForecast) === 1;

  // 客户 / 排柜公司下拉
  const { data: customerGroups = [] } = useRequest(
    async () => {
      if (!addOpen || !factoryInit) return [];
      const result = await getFactoryOwnCustomerInfosForSelectApi({ factoryInit });
      return Array.isArray(result) ? result : [];
    },
    { refreshDeps: [addOpen, factoryInit] },
  );
  const customerOptions = useMemo(
    () => buildCustomerOptions(customerGroups as getFactoryOwnCustomerInfosForSelectResData[]),
    [customerGroups],
  );
  const { data: companyGroups = [] } = useRequest(
    async () => {
      if (!addOpen || !factoryInit) return [];
      const result = await getGoodBoxCompanyInfosComboxApi({ factoryInit, isShowAll: 1 });
      return Array.isArray(result) ? result : [];
    },
    { refreshDeps: [addOpen, factoryInit] },
  );
  const companyOptions = useMemo(
    () => buildCompanyOptions(companyGroups as getGoodBoxCompanyInfosComboxResData[]),
    [companyGroups],
  );

  const watchCustomerID = Form.useWatch('customerID', form);
  const selectedCustomerCode = useMemo(() => {
    if (watchCustomerID === undefined || watchCustomerID === null) return '';
    const groups = customerGroups as getFactoryOwnCustomerInfosForSelectResData[];
    for (const group of groups) {
      const hit = (group.customerInfos ?? []).find(
        (item) => item.customerID === Number(watchCustomerID),
      );
      if (hit) return String(hit.customerCode ?? '');
    }
    return '';
  }, [customerGroups, watchCustomerID]);

  // 打开 / 关闭：初始化候选池与首柜明细
  useEffect(() => {
    if (!addOpen) {
      openedRef.current = false;
      form.resetFields();
      setFileList([]);
      setAllPool([]);
      setCabinets([]);
      setActiveTabKey(FIRST_CABINET_KEY);
      setCreatedGoodBoxNos([]);
      subSeqRef.current = 0;
      return;
    }
    if (!openedRef.current) {
      openedRef.current = true;
      // 候选池直接复用页面「未绑子行列表」已请求到的数据（unboundRows 快照），
      // 再并入页面勾选的行（保证勾选行一定在池中），不重复请求接口
      const poolSource = Array.isArray(unboundRows) ? (unboundRows as SelectedSubItemRow[]) : [];
      const exists = new Set<string>();
      const merged: SelectedSubItemRow[] = [];
      const pushRow = (row: SelectedSubItemRow) => {
        const key = getDetailRowKey(row);
        if (exists.has(key)) return;
        exists.add(key);
        merged.push({ ...row });
      };
      poolSource.forEach(pushRow);
      const picked = (
        Array.isArray(selectedRows) ? (selectedRows as SelectedSubItemRow[]) : []
      ).map((row) => ({ ...row }));
      picked.forEach(pushRow);
      setAllPool(merged);
      // 勾选行直接进入计划总表；实际发货数由用户在总表中填写（默认 0，必填）
      setCabinets([
        {
          key: FIRST_CABINET_KEY,
          type: 'first',
          name: '计划总表',
          // 实际发货数默认取销售数量，可在总表中修改
          rows: picked.map((row) => ({ ...row, f_sendCount: getDetailRowCount(row) })),
        },
      ]);
      setActiveTabKey(FIRST_CABINET_KEY);
    }
    form.setFieldsValue({
      ...(!isForecast && defaultCustomerID !== undefined ? { customerID: defaultCustomerID } : {}),
    });
  }, [addOpen, defaultCustomerID, form, selectedRows, unboundRows, isForecast, setFileList]);

  /** 计划已分配：按 rowKey 汇总所有计划的分配数量（计划总表行不计入） */
  const allocatedMap = useMemo(() => {
    const map = new Map<string, number>();
    cabinets
      .filter((cab) => cab.type === 'sub')
      .forEach((cab) =>
        cab.rows.forEach((row) => {
          const key = getDetailRowKey(row);
          map.set(key, (map.get(key) ?? 0) + (Number(row.f_sendCount) || 0));
        }),
      );
    return map;
  }, [cabinets]);

  /** 计划总表 */
  const mainPlan = useMemo(() => cabinets.find((cab) => cab.type === 'first'), [cabinets]);

  /** 可分配清单的客户过滤口径：优先用表单「所属客户」，未选时回退到页面勾选行的客户 */
  const customerFilterCode = useMemo(() => {
    if (selectedCustomerCode) return selectedCustomerCode;
    const picked = (Array.isArray(selectedRows) ? (selectedRows as SelectedSubItemRow[]) : []).find(
      (row) => String(row.f_customerCode ?? '').trim(),
    );
    return String(picked?.f_customerCode ?? '');
  }, [selectedCustomerCode, selectedRows]);

  const normalizedCustomerCode = customerFilterCode.trim().toUpperCase();
  /**
   * 按客户过滤候选池（忽略大小写/空格）。
   * 严格过滤：选了客户就必须匹配该客户，匹配不到时列表为空（不兜底显示全部），
   * 否则会出现「所选客户下没有数据，清单却列出所有客户」的误导。
   */
  const customerFilteredPool = useMemo(() => {
    if (!normalizedCustomerCode) return allPool;
    return allPool.filter(
      (row) =>
        String(row.f_customerCode ?? '')
          .trim()
          .toUpperCase() === normalizedCustomerCode,
    );
  }, [allPool, normalizedCustomerCode]);

  /**
   * 未绑计划明细清单（计划总表 Tab 下方）：候选池中尚未加入计划总表的行。
   * 加入总表后该行即已「绑定计划」，从清单中消失。
   */
  const unboundPlanRows = useMemo(() => {
    const boundKeys = new Set((mainPlan?.rows ?? []).map((row) => getDetailRowKey(row)));
    return customerFilteredPool.filter((row) => !boundKeys.has(getDetailRowKey(row)));
  }, [customerFilteredPool, mainPlan]);

  /**
   * 待分配清单（计划 Tab 下方）：计划总表中「实际发货数 - 各计划分配合计」> 0 的行。
   * 分配完（待分配 = 0）的子行不再显示。
   */
  const pendingRows = useMemo(() => {
    const rows = (mainPlan?.rows ?? []).map((row) => {
      const total = Number(row.f_sendCount) || 0;
      const allocated = allocatedMap.get(getDetailRowKey(row)) ?? 0;
      return { row, total, allocated, remain: total - allocated };
    });
    return rows.filter((item) => item.remain > 0);
  }, [mainPlan, allocatedMap]);

  /** 当前选中的计划（明细表 / 下方清单的筛选都基于它） */
  const activeCab = useMemo(
    () => cabinets.find((cab) => cab.key === activeTabKey) ?? cabinets[0],
    [cabinets, activeTabKey],
  );

  /** 未绑清单表格数据（自带 key） */
  const unboundTableData = useMemo(
    () => unboundPlanRows.map((row) => ({ row, key: getDetailRowKey(row) })),
    [unboundPlanRows],
  );

  /** 清单筛选数据源：计划总表 → 未绑计划明细清单；计划 → 待分配清单 */
  const poolFilterSource = useMemo<PoolTableRow[]>(
    () => (activeCab?.type === 'sub' ? pendingRows : unboundTableData),
    [activeCab?.type, pendingRows, unboundTableData],
  );

  // ---- Excel 式筛选（需求分类 / 物料料号 / 参考1 / 参考2）：明细表一份、下方清单一份 ----
  const {
    configs: detailFilterConfigs,
    filteredData: detailFilteredRows,
    setSelected: setDetailFilter,
  } = useExcelTextFilters<CabinetRow>(activeCab?.rows ?? [], DETAIL_FILTER_FIELDS);

  const {
    configs: poolFilterConfigs,
    filteredData: poolFilteredRows,
    setSelected: setPoolFilter,
  } = useExcelTextFilters<PoolTableRow>(poolFilterSource, POOL_FILTER_FIELDS);

  // 切换计划时清空筛选，避免上个计划的筛选条件把新计划筛成空表
  const lastFilterCabRef = useRef(activeTabKey);
  useEffect(() => {
    if (lastFilterCabRef.current === activeTabKey) return;
    lastFilterCabRef.current = activeTabKey;
    FILTER_KEYS.forEach((key) => {
      setDetailFilter(key, []);
      setPoolFilter(key, []);
    });
  }, [activeTabKey, setDetailFilter, setPoolFilter]);

  /** 本计划总重 / 全部合计 */
  const cabinetWeight = (cab: Cabinet) =>
    cab.rows.reduce((sum, row) => sum + calcRowWeight(row), 0);
  const totalRows = cabinets.reduce((sum, cab) => sum + cab.rows.length, 0);

  // ---- 计划级操作 ----
  const handleAddCabinet = () => {
    subSeqRef.current += 1;
    const key = `sub-${Date.now()}-${subSeqRef.current}`;
    setCabinets((prev) => [
      ...prev,
      { key, type: 'sub', name: `计划${subSeqRef.current}`, date: null, rows: [] },
    ]);
    setActiveTabKey(key);
  };

  /** 整个删除：仅计划可删；计划总表永不删除 */
  const handleRemoveCabinet = (key: string) => {
    const target = cabinets.find((cab) => cab.key === key);
    if (!target || target.type !== 'sub') {
      message.warning('计划总表为必填，不能删除');
      return;
    }
    setCabinets((prev) => prev.filter((cab) => cab.key !== key));
    if (activeTabKey === key) setActiveTabKey(FIRST_CABINET_KEY);
  };

  // const handleCabinetDateChange = (key: string, date: Dayjs | null) => {
  //   setCabinets((prev) => prev.map((cab) => (cab.key === key ? { ...cab, date } : cab)));
  // };

  // ---- 行级操作 ----
  const handleShipCountChange = (cabKey: string, rowKey: string, value: number | null) => {
    setCabinets((prev) =>
      prev.map((cab) =>
        cab.key === cabKey
          ? {
              ...cab,
              rows: cab.rows.map((row) =>
                getDetailRowKey(row) === rowKey ? { ...row, f_sendCount: value ?? 0 } : row,
              ),
            }
          : cab,
      ),
    );
  };

  const handleRemoveRow = (cabKey: string, rowKey: string) => {
    setCabinets((prev) =>
      prev.map((cab) =>
        cab.key === cabKey
          ? { ...cab, rows: cab.rows.filter((row) => getDetailRowKey(row) !== rowKey) }
          : cab,
      ),
    );
  };

  /** 加入计划总表（总表 Tab 下的未绑计划明细清单）：实际发货数默认取销售数量 */
  const handleAddToMain = (rowKey: string) => {
    const poolRow = unboundPlanRows.find((row) => getDetailRowKey(row) === rowKey);
    if (!mainPlan || !poolRow) return;
    if (mainPlan.rows.some((row) => getDetailRowKey(row) === rowKey)) {
      message.info('该子行已在计划总表');
      return;
    }
    setCabinets((prev) =>
      prev.map((item) =>
        item.key === FIRST_CABINET_KEY
          ? // 加入总表时实际发货数默认取销售数量，可在总表中修改
            {
              ...item,
              rows: [...item.rows, { ...poolRow, f_sendCount: getDetailRowCount(poolRow) }],
            }
          : item,
      ),
    );
  };

  /** 从待分配清单加入本计划：默认填待分配数量；同计划已存在则提示直接改 */
  const handleAddToSub = (cabKey: string, rowKey: string) => {
    const cab = cabinets.find((item) => item.key === cabKey);
    const pending = pendingRows.find((item) => getDetailRowKey(item.row) === rowKey);
    if (!cab || !pending) return;
    if (cab.rows.some((row) => getDetailRowKey(row) === rowKey)) {
      message.info('该子行已在本计划，请直接修改分配数量');
      return;
    }
    setCabinets((prev) =>
      prev.map((item) =>
        item.key === cabKey
          ? {
              ...item,
              rows: [...item.rows, { ...pending.row, f_sendCount: pending.remain }],
            }
          : item,
      ),
    );
  };

  /** 批量加入计划总表（未绑计划明细清单多选）：跳过已绑定的行，实际发货数默认取销售数量 */
  const handleAddSelectedToMain = () => {
    if (!mainPlan) return;
    const keySet = new Set(unboundSelectedKeys.map(String));
    const existing = new Set(mainPlan.rows.map((row) => getDetailRowKey(row)));
    const toAdd = unboundPlanRows.filter(
      (row) => keySet.has(getDetailRowKey(row)) && !existing.has(getDetailRowKey(row)),
    );
    if (toAdd.length === 0) {
      message.info('选中的子行均已在计划总表');
      setUnboundSelectedKeys([]);
      return;
    }
    setCabinets((prev) =>
      prev.map((item) =>
        item.key === FIRST_CABINET_KEY
          ? {
              ...item,
              rows: [
                ...item.rows,
                ...toAdd.map((row) => ({ ...row, f_sendCount: getDetailRowCount(row) })),
              ],
            }
          : item,
      ),
    );
    setUnboundSelectedKeys([]);
  };

  /** 批量加入本计划（待分配清单多选）：跳过本计划已有的行，分配数量默认取各自待分配数 */
  const handleAddSelectedToSub = (cabKey: string) => {
    const cab = cabinets.find((item) => item.key === cabKey);
    if (!cab) return;
    const keySet = new Set(pendingSelectedKeys.map(String));
    const existing = new Set(cab.rows.map((row) => getDetailRowKey(row)));
    const toAdd = pendingRows.filter(
      (pending) =>
        keySet.has(getDetailRowKey(pending.row)) && !existing.has(getDetailRowKey(pending.row)),
    );
    if (toAdd.length === 0) {
      message.info('选中的子行均已在计划中，请直接修改分配数量');
      setPendingSelectedKeys([]);
      return;
    }
    setCabinets((prev) =>
      prev.map((item) =>
        item.key === cabKey
          ? {
              ...item,
              rows: [
                ...item.rows,
                ...toAdd.map((pending) => ({ ...pending.row, f_sendCount: pending.remain })),
              ],
            }
          : item,
      ),
    );
    setPendingSelectedKeys([]);
  };

  // 清单数据变化（加入/移出/过滤）后，清掉已不在清单中的勾选，避免脏选择
  useEffect(() => {
    const valid = new Set(unboundPlanRows.map((row) => getDetailRowKey(row)));
    setUnboundSelectedKeys((keys) => keys.filter((key) => valid.has(String(key))));
  }, [unboundPlanRows]);
  useEffect(() => {
    const valid = new Set(pendingRows.map((item) => getDetailRowKey(item.row)));
    setPendingSelectedKeys((keys) => keys.filter((key) => valid.has(String(key))));
  }, [pendingRows]);

  // 图片预览
  const handlePreview = async (file: UploadFile) => {
    if (file.url) {
      setPreviewSrc(file.url);
      setPreviewOpen(true);
      return;
    }
    if (file.originFileObj) {
      const src = await new Promise<string>((resolve) => {
        const reader = new FileReader();
        reader.onload = () => resolve(String(reader.result ?? ''));
        reader.readAsDataURL(file.originFileObj as RcFile);
      });
      if (src) {
        setPreviewSrc(src);
        setPreviewOpen(true);
      }
    }
  };

  const beforeUpload: UploadProps['beforeUpload'] = (file) => {
    const ok =
      file.type === 'image/jpeg' ||
      file.type === 'image/png' ||
      file.type === 'image/jpg' ||
      file.type === 'image/bmp';
    if (!ok) {
      message.error('只能上传 jpg/png/bmp 格式图片');
      return Upload.LIST_IGNORE;
    }
    addFile(file as RcFile);
    return Upload.LIST_IGNORE;
  };

  useEffect(() => {
    if (!addOpen) return;
    const onPaste = (event: ClipboardEvent) => {
      const items = event.clipboardData?.items;
      if (!items) return;
      const imageFiles: File[] = [];
      for (const item of items) {
        if (item.kind === 'file' && item.type.startsWith('image/')) {
          const file = item.getAsFile();
          if (file) imageFiles.push(file);
        }
      }
      if (imageFiles.length === 0) return;
      event.preventDefault();
      addPastedFiles(imageFiles);
    };
    document.addEventListener('paste', onPaste);
    return () => document.removeEventListener('paste', onPaste);
  }, [addOpen, addPastedFiles]);

  /** 保存/提交评审前置校验：实际发货数必填、计划分配不超实际发货数、客户一致、分配完整性（提示文案按动作区分） */
  const validateBeforeSave = async (
    action: 'save' | 'submit' = 'save',
  ): Promise<AddFormValues | undefined> => {
    const actionText = action === 'submit' ? '提交评审' : '保存';
    if (uploadingCount > 0) {
      message.warning('图片上传中，请稍候再保存');
      return undefined;
    }
    const values = (await form.validateFields()) as AddFormValues;
    const allRows = cabinets.flatMap((cab) => cab.rows);

    const detailCustomerCodes = Array.from(
      new Set(allRows.map((row) => String(row.f_customerCode ?? '')).filter(Boolean)),
    );
    if (detailCustomerCodes.length > 1) {
      message.error(`明细包含多个客户（${detailCustomerCodes.join('、')}），请先移除其它客户的行`);
      return undefined;
    }
    if (
      selectedCustomerCode &&
      detailCustomerCodes.length === 1 &&
      detailCustomerCodes[0] !== selectedCustomerCode
    ) {
      message.error(
        `所属客户（${selectedCustomerCode}）与明细客户（${detailCustomerCodes[0]}）不一致`,
      );
      return undefined;
    }

    // 计划总表：实际发货数必填（> 0）
    const emptyTotal = (mainPlan?.rows ?? []).find((row) => !(Number(row.f_sendCount) > 0));
    if (emptyTotal) {
      message.error(
        `子行【${emptyTotal.f_materialCode ?? getDetailRowKey(emptyTotal)}】未填写实际发货数`,
      );
      return undefined;
    }
    // 计划：分配数量必填（≥ 0 的有效数）
    const invalidSub = cabinets
      .filter((cab) => cab.type === 'sub')
      .flatMap((cab) => cab.rows)
      .find(
        (row) =>
          row.f_sendCount === null ||
          row.f_sendCount === undefined ||
          !Number.isFinite(Number(row.f_sendCount)) ||
          Number(row.f_sendCount) < 0,
      );
    if (invalidSub) {
      message.error(
        `计划行【${invalidSub.f_materialCode ?? getDetailRowKey(invalidSub)}】未填写有效分配数量，请补全`,
      );
      return undefined;
    }
    // 分配校验：各计划分配合计 ≤ 计划总表实际发货数
    const over = (mainPlan?.rows ?? []).find((row) => {
      const total = Number(row.f_sendCount) || 0;
      return (allocatedMap.get(getDetailRowKey(row)) ?? 0) > total;
    });
    if (over) {
      const key = getDetailRowKey(over);
      message.error(
        `子行【${over.f_materialCode ?? key}】计划分配合计 ${allocatedMap.get(key)} 超过实际发货数 ${over.f_sendCount}`,
      );
      return undefined;
    }
    // 计划分配完整性校验（保存 / 提交评审共用）：
    // - 没建计划：只有计划总表，直接允许保存；
    // - 建了计划且已分配，但待分配清单还有剩余：拦截，必须分配完才能保存/提交；
    // - 建了计划但一个明细都没分配：二次确认，「继续分配」中断操作，「不拆分」按空计划跳过继续保存
    const subs = cabinets.filter((cab) => cab.type === 'sub');
    if (subs.length > 0) {
      const hasAllocated = subs.some((cab) => cab.rows.length > 0);
      if (!hasAllocated) {
        const confirmed = await new Promise<boolean>((resolve) => {
          Modal.confirm({
            title: '提示',
            centered: true,
            content: '已新建计划但没有分配清单，是否要分配？',
            okText: '继续分配',
            cancelText: '不拆分计划',
            onOk: () => resolve(false),
            onCancel: () => resolve(true),
          });
        });
        if (!confirmed) return undefined;
      } else if (pendingRows.length > 0) {
        message.error(
          `还有 ${pendingRows.length} 行明细未分配完，计划分配总数需等于计划总表实际发货数，请在待分配清单完成分配后再${actionText}`,
        );
        return undefined;
      }
    }
    return values;
  };

  /** 逐计划创建单据：计划总表携图片、用表单顶部日期；计划用各自日期（选填，回退顶部日期） */
  const createAllCabinets = async (values: AddFormValues): Promise<string[]> => {
    const topDate = values.f_goodBoxDate
      ? dayjs(values.f_goodBoxDate as Dayjs).format('YYYY-MM-DD')
      : '';
    const nos: string[] = [];
    for (const cab of cabinets) {
      if (cab.rows.length === 0) continue;
      const formData = cab.rows.map((row) => ({
        ...row,
        f_sendCount: isForecast ? row.f_sendCount : Number(row.f_sendCount),
        f_sumWeight: calcRowWeight(row),
      }));
      const boxDate =
        cab.type === 'first' ? topDate : cab.date ? dayjs(cab.date).format('YYYY-MM-DD') : topDate;
      // const result = await quickCreateGoodBoxApi({
      //   goodBoxInfo: {
      //     goodBoxDate: boxDate,
      //     goodBoxDescription: (values.f_goodBoxDescription as string) ?? '',
      //     file: '',
      //     factoryInit,
      //     isForecastGoodBox: defaultIsForecast,
      //     ticketNo: (values.ticketNo as string) ?? '',
      //     companyInit: values.companyInit ?? 0,
      //     // 图片仅随计划总表提交，计划不重复上传，避免重复图片记录
      //     saveImageNames: cab.type === 'first' ? getSaveImageNames() : [],
      //   },
      //   formData: formData as unknown as Array<Record<string, unknown>>,
      // });
      // const no = String((result as { data?: unknown })?.data ?? '').trim();
      // if (!no) throw new Error(`${cab.name}创建失败，未返回计划单号`);
      // nos.push(no);
    }
    return nos;
  };

  const handleSave = async () => {
    if (totalRows === 0) {
      message.warning('计划总表及计划明细均为空，请先添加明细子行');
      return;
    }
    setSaveLoading(true);
    try {
      const values = await validateBeforeSave('save');
      if (!values) return;
      const nos = await createAllCabinets(values);
      if (nos.length === 0) {
        message.warning('没有可保存的计划（空计划已跳过）');
        return;
      }
      setCreatedGoodBoxNos(nos);
      message.success(`新建 ${nos.length} 个发货计划成功：${nos.join('、')}`);
      onCancel();
      onOk(nos[0]);
    } catch (error) {
      message.error(error instanceof Error ? error.message : '保存失败，请稍后重试');
    } finally {
      setSaveLoading(false);
    }
  };

  /** 评审失败后按单号回查已绑定明细，刷新对应计划（展示可提交/提交失败原因） */
  const reloadBoundDetail = async (nos: string[]) => {
    setDetailLoading(true);
    try {
      const emptyQuery = {
        materialCode: '',
        materialCode1: '',
        materialCode2: '',
        demandTypeNo: '',
        detailOrderNo: '',
        detailID: '',
      };
      const refreshed = await Promise.all(
        cabinets.map(async (cab, index) => {
          const no = nos[index];
          if (!no || cab.rows.length === 0) return cab;
          const result = isForecast
            ? await getGoodBoxBindForecastItemsApi({ goodBoxNo: no, formData: emptyQuery })
            : await getGoodBoxBindSaleSubItemInfosApi({ goodBoxNo: no, formData: emptyQuery });
          const rows = Array.isArray(result) ? (result as unknown as SelectedSubItemRow[]) : [];
          return {
            ...cab,
            rows: rows.map((row) => ({
              ...row,
              f_sendCount: row.f_sendCount ?? getDetailRowCount(row),
            })),
          } as Cabinet;
        }),
      );
      setCabinets(refreshed);
    } catch {
      // 回查失败不覆盖现有明细（错误已由 request 拦截器提示）
    } finally {
      setDetailLoading(false);
    }
  };

  const handleSubmitReview = async () => {
    if (totalRows === 0) {
      message.warning('计划总表及计划明细均为空，请先添加明细子行');
      return;
    }
    setSubmitLoading(true);
    try {
      const values = await validateBeforeSave('submit');
      if (!values) return;
      const nos = await createAllCabinets(values);
      if (nos.length === 0) {
        message.warning('没有可提交的柜（空柜已跳过）');
        return;
      }
      setCreatedGoodBoxNos(nos);
      // const results = await Promise.all(nos.map((no) => goodBoxReviewApi({ goodBoxNo: no })));
      // const failed = results
      //   .map((res, idx) => (res?.code === 200 ? null : `${nos[idx]}：${res?.message || '失败'}`))
        // .filter(Boolean) as string[];
      // if (failed.length === 0) {
      //   message.success('提交评审成功');
      //   onCancel();
      //   onOk(nos[0]);
      // } else {
      //   message.error(`以下计划提交评审未通过：${failed.join('；')}`);
      //   await reloadBoundDetail(nos);
      // }
    } catch (error) {
      message.error(error instanceof Error ? error.message : '提交评审失败，请稍后重试');
    } finally {
      setSubmitLoading(false);
    }
  };

  // ---- 明细表列（按计划生成：计划总表列头「实际发货数」，计划「分配数量」） ----
  const buildColumns = (cab: Cabinet): NonNullable<TableProps<CabinetRow>['columns']> => [
    {
      title: '需求分类',
      key: 'f_demandTypeNo',
      dataIndex: 'f_demandTypeNo',
      width: 140,
      fixed: 'left',
      ellipsis: true,
    },
    { title: '行号', dataIndex: 'f_itemLineNo', width: 60, fixed: 'left' },
    {
      title: '物料料号',
      key: 'f_materialCode',
      dataIndex: 'f_materialCode',
      ellipsis: true,
      width: 150,
    },
    {
      title: '参考1',
      key: 'f_materialCode1',
      dataIndex: 'f_materialCode1',
      width: 70,
      ellipsis: true,
    },
    {
      title: '参考2',
      key: 'f_materialCode2',
      dataIndex: 'f_materialCode2',
      width: 120,
      ellipsis: true,
    },
    {
      title: isForecast ? '预测数量' : '销售数量',
      key: 'count',
      width: 80,
      render: (_, row) => <span className={styles.countNum}>{getDetailRowCount(row)}</span>,
    },
    {
      title: cab.type === 'first' ? '实际发货数' : '分配数量',
      key: 'f_sendCount',
      width: 110,
      render: (_: unknown, row: CabinetRow) => {
        const key = getDetailRowKey(row);
        // 分配超发：各计划分配合计 > 计划总表实际发货数（当前行未填时不计）
        const total = (mainPlan?.rows ?? []).find((r) => getDetailRowKey(r) === key)?.f_sendCount;
        const hasTotal = Number(total) > 0;
        const over = hasTotal && (allocatedMap.get(key) ?? 0) > Number(total);
        // 计划分配上限：实际发货数 - 其它计划已分配（本行当前值不计入）。
        // 注意：不能把 max 交给 InputNumber —— antd 带 max 时超限输入不触发 onChange，提示就没了；
        // 改为 onChange 内自行截断，输入/粘贴/步进按钮都会先经过这里
        const max =
          cab.type === 'sub' && hasTotal
            ? Math.max(
                0,
                Number(total) - (allocatedMap.get(key) ?? 0) + (Number(row.f_sendCount) || 0),
              )
            : undefined;
        // 超上限时说明截断原因（节流：连续键入只提示一次）
        const warnAllocationLimit = () => {
          const now = Date.now();
          if (now - limitWarnRef.current < 1000) return;
          limitWarnRef.current = now;
          message.warning(`所有计划分配合计不能超过实际发货数${total}`);
        };
        return (
          <InputNumber
            size="small"
            min={0}
            className={over ? styles.overInput : undefined}
            style={{ width: '100%' }}
            value={row.f_sendCount}
            placeholder="0"
            onChange={(value) => {
              if (max !== undefined && value !== null && Number(value) > max) {
                warnAllocationLimit();
                handleShipCountChange(cab.key, key, max);
                return;
              }
              handleShipCountChange(cab.key, key, value);
            }}
          />
        );
      },
    },
    {
      title: '累计已分·剩余',
      key: 'allocated',
      width: 110,
      render: (_, row) => {
        const key = getDetailRowKey(row);
        const total = Number(
          (mainPlan?.rows ?? []).find((r) => getDetailRowKey(r) === key)?.f_sendCount ?? 0,
        );
        const allocated = allocatedMap.get(key) ?? 0;
        const remain = total - allocated;
        return (
          // 剩余 >0 橙色提醒；=0 默认色；<0 超发红色
          <span
            className={remain < 0 ? styles.overText : remain > 0 ? styles.remainPending : undefined}
          >
            {allocated} · 剩 {remain}
          </span>
        );
      },
    },
    {
      title: '单位',
      key: 'unit',
      width: 60,
      render: (_, row) => <span>{getDetailRowUnit(row)}</span>,
    },
    {
      title: '理论重量',
      dataIndex: 'f_standardWeight',
      width: 80,
    },
    {
      title: '总重',
      key: 'f_sumWeight',
      width: 80,
      render: (_, row) => <span>{formatTotal(calcRowWeight(row))}</span>,
    },
    { title: '重量单位', dataIndex: 'f_weightUnitName', key: 'f_weightUnitName', width: 70 },
    {
      title: '可提交',
      dataIndex: 'f_isCanSubmit',
      key: 'f_isCanSubmit',
      width: 65,
      render: (value: number) =>
        value === 1 ? (
          <Tag color="green">是</Tag>
        ) : value === 0 ? (
          <Tag color="error">否</Tag>
        ) : (
          <></>
        ),
    },
    {
      title: '提交说明',
      dataIndex: 'f_submitFailReason',
      key: 'f_submitFailReason',
      width: 130,
      ellipsis: true,
    },
    {
      title: '操作',
      key: 'action',
      width: 65,
      align: 'center',
      fixed: 'right',
      render: (_, row) => (
        <Button
          color="danger"
          variant="filled"
          size="small"
          onClick={() => handleRemoveRow(cab.key, getDetailRowKey(row))}
        >
          移除
        </Button>
      ),
    },
  ];

  /**
   * 未绑计划明细清单列（计划总表 Tab 下方）：明细字段 + 加入计划总表。
   * 首列不做 fixed：清单加了多选列后，虚拟表格的 sticky 偏移会把首列文字压到选择列下方（被裁切）
   */
  const poolColumns: TableProps<{ row: SelectedSubItemRow; key: string }>['columns'] = [
    {
      title: '需求分类',
      key: 'f_demandTypeNo',
      dataIndex: ['row', 'f_demandTypeNo'],
      width: 140,
      ellipsis: true,
    },
    { title: '行号', dataIndex: ['row', 'f_itemLineNo'], width: 60 },

    { title: '客户代码', dataIndex: ['row', 'f_customerCode'], width: 90, ellipsis: true },

    {
      title: '物料料号',
      key: 'f_materialCode',
      dataIndex: ['row', 'f_materialCode'],
      width: 170,
      ellipsis: true,
    },
    {
      title: '参考1',
      key: 'f_materialCode1',
      dataIndex: ['row', 'f_materialCode1'],
      width: 70,
      ellipsis: true,
    },
    {
      title: '参考2',
      key: 'f_materialCode2',
      dataIndex: ['row', 'f_materialCode2'],
      width: 120,
      ellipsis: true,
    },
    {
      title: '客户料号',
      dataIndex: ['row', 'f_customerMaterialNo'],
      width: 120,
      ellipsis: true,
    },
    {
      title: '客户料名',
      dataIndex: ['row', 'f_customerMaterialName'],
      width: 120,
      ellipsis: true,
    },
    {
      title: '交货日期',
      key: 'deliverDate',
      width: 100,
      render: (_, item) => item.row.f_deliverDate ?? item.row.f_planShipDate ?? '',
    },
    { title: '物料名称', dataIndex: ['row', 'f_materialName'], width: 120, ellipsis: true },
    {
      title: isForecast ? '预测数量' : '销售数量',
      key: 'sale',
      width: 90,
      render: (_, item) => <span className={styles.countNum}>{getDetailRowCount(item.row)}</span>,
    },
    {
      title: '单位',
      key: 'unit',
      width: 60,
      render: (_, item) => getDetailRowUnit(item.row),
    },
    {
      title: '库存数量',
      key: 'storeCount',
      width: 80,
      render: (_, item) => item.row.f_storeCount ?? '',
    },
    {
      title: '计划状态',
      key: 'planStatus',
      width: 90,
      render: (_, item) => item.row.f_planStatusName ?? item.row.f_orderStatusTitle ?? '',
    },
    {
      title: '单件重量',
      key: 'weight',
      width: 80,
      render: (_, item) => formatTotal(Number(item.row.f_weight ?? item.row.f_standardWeight ?? 0)),
    },
    {
      title: '销售总重',
      key: 'sumWeight',
      width: 90,
      render: (_, item) => formatTotal(Number(item.row.f_sumWeight ?? 0)),
    },
    {
      title: '操作',
      key: 'action',
      width: 70,
      align: 'center',
      fixed: 'right',
      render: (_, item) => (
        <Button type="link" size="small" onClick={() => handleAddToMain(item.key)}>
          加入总表
        </Button>
      ),
    },
  ];

  /**
   * 待分配清单列（计划 Tab 下方）：需求分类/行号 | 实际发货数/已分配·待分配（原客户代码位置）
   * | 交货日期/物料名称/物料料号/参考1/参考2 | 客户代码（后移到客户料号前）| 客户料号/…/销售总重 | 加入本计划。
   * poolColumns 列序：需求分类(0)/行号(1)/客户代码(2)/交货日期(3)/物料名称(4)/物料料号(5)/参考1(6)/参考2(7)
   * | 客户料号(8)/客户料名(9)/销售数量(10)/单位(11)/库存数量(12)/计划状态(13)/单件重量(14)/销售总重(15) | 操作
   */
  const assignedColumns: TableProps<(typeof pendingRows)[number]>['columns'] = [
    ...((poolColumns.slice(0, 2) as unknown as TableProps<
      (typeof pendingRows)[number]
    >['columns']) ?? []),
    // {
    //   title: '实际发货数',
    //   key: 'total',
    //   width: 100,
    //   render: (_, item) => <span className={styles.countNum}>{item.total}</span>,
    // },
    {
      title: '已分配 · 待分配',
      key: 'allocated',
      width: 120,
      // 清单只显示待分配 >0 的行，恒为橙色提醒
      render: (_, item) => (
        <span className={styles.remainPending}>
          {item.allocated} · 待分 {item.remain}
        </span>
      ),
    },
    ...((poolColumns.slice(3, 8) as unknown as TableProps<
      (typeof pendingRows)[number]
    >['columns']) ?? []),
    ...((poolColumns.slice(2, 3) as unknown as TableProps<
      (typeof pendingRows)[number]
    >['columns']) ?? []),
    ...((poolColumns.slice(8, -1) as unknown as TableProps<
      (typeof pendingRows)[number]
    >['columns']) ?? []),
    {
      title: '操作',
      key: 'action',
      width: 80,
      align: 'center',
      fixed: 'right',
      render: (_, item) => (
        <Button
          type="link"
          size="small"
          onClick={() => handleAddToSub(activeTabKey, getDetailRowKey(item.row))}
        >
          加入计划
        </Button>
      ),
    },
  ];

  /** 单个计划的 Tab 面板：计划信息 + 明细表（含合计） + 下方清单 */
  const renderCabinetPanel = (cab: Cabinet) => {
    // 清单表体高度 = 页签内容区实测高 - 表头高（48，与 MESTable 内部 minHeight 的口径一致），
    // 容器正好撑满内容区：既不留空白，也不会把最后一行顶到面板外
    const poolScrollY: number | string =
      poolHolderHeight > 0 ? Math.max(0, poolHolderHeight - 72) : 234;
    // 明细表列：挂上「需求分类 / 物料料号 / 参考1 / 参考2」的 Excel 式筛选
    const columns = applyExcelTextFilters(buildColumns(cab), detailFilterConfigs);
    const colCount = columns.length;
    const weightColIndex = isForecast ? 9 : 10; // 「累计已分·剩余」后移一位，总重列随之变化

    // 清单筛选后的可见行（全选只作用于可见行）
    const visibleUnboundRows = (cab.type === 'first' ? poolFilteredRows : []) as {
      row: SelectedSubItemRow;
      key: string;
    }[];
    const visiblePendingRows = (cab.type === 'sub' ? poolFilteredRows : []) as typeof pendingRows;
    const pendingKeys = visiblePendingRows.map((item) => getDetailRowKey(item.row));

    // 清单多选：自定义勾选列（虚拟表格下 antd 内置 rowSelection 列宽度失效，checkbox 会被首列压住）
    const allUnboundSelected =
      visibleUnboundRows.length > 0 &&
      visibleUnboundRows.every((item) => unboundSelectedKeys.includes(item.key));
    const allPendingSelected =
      pendingKeys.length > 0 && pendingKeys.every((key) => pendingSelectedKeys.includes(key));

    const unboundColumns: TableProps<{ row: SelectedSubItemRow; key: string }>['columns'] = [
      {
        title: (
          <Checkbox
            checked={allUnboundSelected}
            indeterminate={
              !allUnboundSelected &&
              visibleUnboundRows.some((item) => unboundSelectedKeys.includes(item.key))
            }
            onChange={(event) =>
              setUnboundSelectedKeys(
                event.target.checked ? visibleUnboundRows.map((item) => item.key) : [],
              )
            }
          />
        ),
        key: '__unbound_selection__',
        width: 44,
        align: 'center',
        render: (_, item) => (
          <Checkbox
            checked={unboundSelectedKeys.includes(item.key)}
            onChange={(event) =>
              setUnboundSelectedKeys((prev) =>
                event.target.checked ? [...prev, item.key] : prev.filter((key) => key !== item.key),
              )
            }
          />
        ),
      },
      ...(applyExcelTextFilters(poolColumns ?? [], poolFilterConfigs) as NonNullable<
        TableProps<{ row: SelectedSubItemRow; key: string }>['columns']
      >),
    ];

    const pendingColumns: TableProps<(typeof pendingRows)[number]>['columns'] = [
      {
        title: (
          <Checkbox
            checked={allPendingSelected}
            indeterminate={
              !allPendingSelected && pendingKeys.some((key) => pendingSelectedKeys.includes(key))
            }
            onChange={(event) => setPendingSelectedKeys(event.target.checked ? pendingKeys : [])}
          />
        ),
        key: '__pending_selection__',
        width: 44,
        align: 'center',
        render: (_, item) => {
          const key = getDetailRowKey(item.row);
          return (
            <Checkbox
              checked={pendingSelectedKeys.includes(key)}
              onChange={(event) =>
                setPendingSelectedKeys((prev) =>
                  event.target.checked ? [...prev, key] : prev.filter((k) => k !== key),
                )
              }
            />
          );
        },
      },
      ...(applyExcelTextFilters(assignedColumns ?? [], poolFilterConfigs) as NonNullable<
        TableProps<(typeof pendingRows)[number]>['columns']
      >),
    ];
    return (
      <div className={styles.cabinetPanel} ref={cabinetPanelRef}>
        {/* <div className={styles.cabinetHeader}>
          <span
            className={`${styles.cabinetTag} ${cab.type === 'first' ? styles.firstTag : styles.subTag}`}
          >
            {cab.name}
          </span> */}
        {/* {cab.type === 'sub' && (
          <span className={styles.dateField}>
            <span className={styles.dateLabel}>计划日期</span>
            <DatePicker
              size="small"
              value={cab.date ?? null}
              format="YYYY-MM-DD"
              placeholder="预留，可不填"
              minDate={dayjs().startOf('day')}
              onChange={(date) => handleCabinetDateChange(cab.key, date)}
            />
          </span>
        )} */}
        {/* {cab.type === 'first' && (
            <span className={styles.dateLabel}>日期使用顶部「货柜日期」</span>
          )} 
          <span className={styles.headerStat}>
            {cab.rows.length} 项 · {formatTotal(cabinetWeight(cab))} KG
          </span>
          {cab.type === 'sub' && (
            <Button
              type="link"
              danger
              size="small"
              icon={<DeleteOutlined />}
              onClick={() => handleRemoveCabinet(cab.key)}
            >
              删除本柜
            </Button>
          )}
        </div>*/}

        <MESTable<CabinetRow>
          rowKey={getDetailRowKey}
          size="small"
          columns={columns}
          dataSource={detailFilteredRows}
          pagination={false}
          loading={detailLoading}
          scroll={{ y: detailTableHeight, x: 1500 }}
          locale={{
            emptyText:
              cab.type === 'first'
                ? '计划总表暂无明细，可从下方「未绑计划明细清单」加入'
                : '本计划暂无明细，可从下方「待分配清单」加入',
          }}
          summary={() => (
            <Table.Summary fixed>
              <Table.Summary.Row>
                <Table.Summary.Cell index={0} colSpan={weightColIndex}>
                  合计
                </Table.Summary.Cell>
                <Table.Summary.Cell index={weightColIndex}>
                  {formatTotal(cabinetWeight(cab))}
                </Table.Summary.Cell>
                <Table.Summary.Cell
                  index={weightColIndex + 1}
                  colSpan={colCount - weightColIndex - 1}
                />
              </Table.Summary.Row>
            </Table.Summary>
          )}
        />
        {/* 列数 / 滚动提示：右栏变窄后明细列较多，明确告知可横向滚动 */}
        {/* <div className={styles.scrollHint}>{columns.length} 列 · 固定列宽可横向滚动</div> */}

        {/* 明细表与下方清单之间的拖拽分隔条：上下拖动调整两张表的高度占比 */}
        <div
          className={styles.panelResizer}
          role="separator"
          aria-orientation="horizontal"
          title="拖动调整明细表高度"
          onMouseDown={handleDetailResizeStart}
        >
          <span className={styles.panelResizerGrip} />
        </div>

        {/* 下方清单随当前页签切换：计划总表 → 未绑计划明细清单；计划 → 待分配清单。
            两个清单均支持多选勾选，批量加入计划总表 / 本计划（按钮在页签栏左侧） */}
        <div className={styles.poolPanel} ref={poolPanelRef}>
          <Tabs
            size="small"
            activeKey={cab.type === 'first' ? 'unbound' : 'pending'}
            tabBarExtraContent={{
              /* 批量操作区放页签栏右侧，与清单页签同一行 */
              right:
                cab.type === 'first' ? (
                  <Space size={8}>
                    <span className={styles.poolHint}>已选 {unboundSelectedKeys.length} 行</span>
                    <Button
                      type="primary"
                      size="small"
                      ghost
                      disabled={unboundSelectedKeys.length === 0}
                      onClick={handleAddSelectedToMain}
                    >
                      加入计划总表
                    </Button>
                  </Space>
                ) : (
                  <Space size={8}>
                    <span className={styles.poolHint}>已选 {pendingSelectedKeys.length} 行</span>
                    <Button
                      type="primary"
                      size="small"
                      ghost
                      disabled={pendingSelectedKeys.length === 0}
                      onClick={() => handleAddSelectedToSub(cab.key)}
                    >
                      加入本计划
                    </Button>
                  </Space>
                ),
            }}
            items={
              cab.type === 'first'
                ? [
                    {
                      key: 'unbound',
                      label: (
                        <span className={styles.tabLabel}>
                          未绑计划明细清单
                          <span className={styles.tabCount}>{visibleUnboundRows.length}</span>
                        </span>
                      ),
                      children: (
                        // 未加入计划总表的明细子行，支持勾选多行批量加入
                        <MESTable<{ row: SelectedSubItemRow; key: string }>
                          rowKey="key"
                          size="small"
                          columns={unboundColumns}
                          dataSource={visibleUnboundRows}
                          pagination={false}
                          // 表体高度 = 页签内容区实测高 - 表头高，正好占满清单面板
                          scroll={{ y: poolScrollY, x: 1700 }}
                          virtual
                          locale={{
                            emptyText: normalizedCustomerCode
                              ? `客户【${customerFilterCode}】下暂无未绑计划的明细子行`
                              : '暂无未绑计划明细：未查询到未绑计划的明细子行',
                          }}
                        />
                      ),
                    },
                  ]
                : [
                    {
                      key: 'pending',
                      label: (
                        <span className={styles.tabLabel}>
                          待分配清单
                          <span className={styles.tabCount}>{visiblePendingRows.length}</span>
                        </span>
                      ),
                      children: (
                        // 计划总表中待分配（实际发货数 - 已分配 > 0）的子行；分配完的行不再显示
                        <MESTable<(typeof pendingRows)[number]>
                          rowKey={(item) => getDetailRowKey(item.row)}
                          size="small"
                          columns={pendingColumns}
                          dataSource={visiblePendingRows}
                          pagination={false}
                          // 表体高度 = 页签内容区实测高 - 表头高，正好占满清单面板
                          scroll={{ y: poolScrollY, x: 1700 }}
                          virtual
                          locale={{
                            emptyText: '暂无待分配子行：请先在计划总表加入明细并填写实际发货数',
                          }}
                        />
                      ),
                    },
                  ]
            }
          />
        </div>
      </div>
    );
  };

  /**
   * 计划胶囊栏（主从视觉）：
   * - 左：计划总表 = 母单（实心蓝胶囊，不可关闭）；
   * - 中：「拆分视图：」标签；
   * - 右：计划胶囊（虚线边框，选中变实线蓝；带行数徽标与删除 ×）；
   * - 末尾：「+ 新增计划」。
   * 胶囊下方按当前选中计划渲染明细面板。
   */
  const renderPlanBar = () => {
    const subs = cabinets.filter((cab) => cab.type === 'sub');
    return (
      <>
        <div className={styles.planBar}>
          <button
            type="button"
            className={`${styles.planMaster} ${
              activeCab?.type === 'first' ? styles.planMasterActive : ''
            }`}
            onClick={() => setActiveTabKey(FIRST_CABINET_KEY)}
          >
            <ProfileOutlined />
            <span className={styles.planMasterText}>计划总表</span>
            <span className={styles.planBadge}>{mainPlan?.rows.length ?? 0}</span>
          </button>
          <span className={styles.planSplitLabel}>拆分计划：</span>
          <div className={styles.planSubList}>
            {subs.map((cab, index) => {
              // 名称形如「计划2」，序号取名称中的数字，取不到时用顺序号兜底
              const seq = Number(String(cab.name).match(/\d+/)?.[0]) || index + 1;
              const active = activeCab?.key === cab.key;
              return (
                <span
                  key={cab.key}
                  className={`${styles.planSub} ${active ? styles.planSubActive : ''}`}
                  onClick={() => setActiveTabKey(cab.key)}
                >
                  <span className={styles.planSubText}>计划{seq}</span>
                  <span className={styles.planSubBadge}>{cab.rows.length}</span>
                  <span
                    className={styles.planSubClose}
                    title="删除本计划"
                    onClick={(event) => {
                      event.stopPropagation();
                      handleRemoveCabinet(cab.key);
                    }}
                  >
                    <CloseOutlined />
                  </span>
                </span>
              );
            })}
          </div>
          {/* 新增按钮固定在胶囊栏右侧，不随计划胶囊一起横向滚动 */}
          <Button
            type="link"
            size="small"
            className={styles.planAddBtn}
            icon={<PlusOutlined />}
            onClick={handleAddCabinet}
          >
            新增计划
          </Button>
        </div>
        <div className={styles.planBody}>{activeCab ? renderCabinetPanel(activeCab) : null}</div>
      </>
    );
  };

  // 抽屉挂到 .layout-container（与页面 createPortal 目标一致），
  // absolute 定位贴合布局容器，避免 body 级 fixed 抽屉被 KeepAlive 隐藏残留
  const getDrawerContainer = () =>
    document.querySelector<HTMLElement>('.layout-container') || document.body;

  return (
    <Drawer
      title={<span>{isForecast ? '新建预测发货计划' : '新建发货计划'}</span>}
      open={addOpen}
      onClose={onCancel}
      size={'94%'}
      getContainer={getDrawerContainer}
      closable={{ placement: 'end' }}
      rootStyle={{ position: 'absolute', borderTop: '1px solid #eee' }}
      /* 两栏布局贴合边缘，收紧 body 内边距；body 自身作为 flex 容器，
         让左右两栏撑满可视高度、内容在内部滚动（不出现抽屉外层滚动条） */
      styles={{
        body: {
          display: 'flex',
          flexDirection: 'column',
          padding: 12,
          minHeight: 0,
          overflow: 'hidden',
        },
      }}
      footer={
        <div style={{ textAlign: 'right' }}>
          <Space>
            <Button onClick={onCancel}>取消</Button>
            <Button
              type="primary"
              ghost
              loading={saveLoading}
              disabled={totalRows === 0 || uploadingCount > 0}
              onClick={handleSave}
            >
              保存
            </Button>
            <Button
              type="primary"
              loading={submitLoading}
              disabled={totalRows === 0 || uploadingCount > 0}
              onClick={handleSubmitReview}
            >
              提交评审
            </Button>
          </Space>
        </div>
      }
    >
      {/* 左右两栏：左侧整单公共信息（纵向表单 + 图片），右侧柜体 Tab 与明细 */}
      <div className={styles.layout}>
        <Form
          form={form}
          autoComplete="off"
          layout="vertical"
          className={`${styles.formPanel} ${styles.leftPanel}`}
        >
          <Form.Item label="所属客户" name="customerID">
            <Select
              //   disabled={!isForecast}
              options={customerOptions}
              showSearch={{ optionFilterProp: 'label' }}
              placeholder="请选择所属客户"
              allowClear
            />
          </Form.Item>
          <Form.Item
            label="计划日期"
            name="f_goodBoxDate"
            rules={[{ required: true, message: '请选择计划日期' }]}
          >
            <DatePicker
              style={{ width: '100%' }}
              format="YYYY-MM-DD"
              placeholder="计划日期"
              minDate={dayjs().startOf('day')}
              allowClear={false}
            />
          </Form.Item>
          <Form.Item label="发票号" name="ticketNo">
            <Input allowClear placeholder="发票号" />
          </Form.Item>
          <Form.Item
            label="排柜公司"
            name="companyInit"
            rules={[{ required: true, message: '请选择排柜公司' }]}
          >
            <Select
              options={companyOptions}
              showSearch={{ optionFilterProp: 'label' }}
              placeholder="请选择排柜公司"
            />
          </Form.Item>
          <Form.Item label="备注" name="f_goodBoxDescription">
            <Input.TextArea
              rows={2}
              placeholder="请输入备注（装箱要求、贴标标准等）"
              style={{ width: '100%' }}
            />
          </Form.Item>
          <Form.Item
            label="附加图片"
            colon={false}
            extra={`可点击上传，也可直接 Ctrl+V 粘贴（最多 ${MAX_IMAGE_COUNT} 张）`}
          >
            <Upload
              className={styles.imageUpload}
              accept="image/*"
              multiple
              listType="picture-card"
              fileList={fileList}
              beforeUpload={beforeUpload}
              onChange={({ fileList: next }) => setFileList([...next])}
              onPreview={handlePreview}
            >
              {fileList.length >= MAX_IMAGE_COUNT ? null : (
                <div>
                  <PlusOutlined />
                  <div style={{ marginTop: 8 }}>上传图片</div>
                </div>
              )}
            </Upload>
          </Form.Item>
        </Form>

        <div className={styles.rightPanel}>
          {/* 柜号提示（仅评审失败回显时出现） */}
          {createdGoodBoxNos.length > 0 && (
            <div className={styles.cabinetAreaHeader}>
              <Space size={12} align="center" className={styles.headerRight}>
                <span className={styles.goodBoxNo} title="本次新建的计划单号">
                  单号：{createdGoodBoxNos.join('、')}
                </span>
              </Space>
            </div>
          )}
          {renderPlanBar()}
        </div>
      </div>

      {previewSrc && (
        <Image
          style={{ display: 'none' }}
          src={previewSrc}
          preview={{
            visible: previewOpen,
            onVisibleChange: (visible) => {
              setPreviewOpen(visible);
              if (!visible) setPreviewSrc('');
            },
          }}
        />
      )}
    </Drawer>
  );
};

export default AddSendPlanDrawer;
