export const issueStateLabels: Record<string, string> = {
  new:'待处理', needs_info:'待补充信息', triaged:'已分诊', in_progress:'处理中', resolved:'已解决', closed:'已关闭',
}

export const issueActions: Record<string, {state:string; label:string}[]> = {
  new:[{state:'needs_info',label:'需要补充信息'}, {state:'triaged',label:'标记已分诊'}],
  needs_info:[{state:'triaged',label:'标记已分诊'}],
  triaged:[{state:'needs_info',label:'需要补充信息'}, {state:'in_progress',label:'开始处理'}],
  in_progress:[{state:'needs_info',label:'需要补充信息'}, {state:'resolved',label:'标记已解决'}],
  resolved:[{state:'in_progress',label:'重新打开'}, {state:'closed',label:'关闭问题'}],
  closed:[],
}
