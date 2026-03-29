import { createFileRoute, Link } from '@tanstack/react-router'
import { useEffect, useState } from 'react'
import { listTasks, getTaskExportXlsxUrl, type TaskListItem } from '@/libs/api'

export const Route = createFileRoute('/admin/')({
	component: AdminPage,
})

function AdminPage() {
	const [tasks, setTasks] = useState<TaskListItem[]>([])
	const [err, setErr] = useState<string | null>(null)
	const [loading, setLoading] = useState(true)

	useEffect(() => {
		let cancelled = false
		;(async () => {
			try {
				const data = await listTasks({ limit: 100 })
				if (!cancelled) setTasks(data.tasks)
			} catch (e: unknown) {
				if (!cancelled)
					setErr(e instanceof Error ? e.message : '載入失敗')
			} finally {
				if (!cancelled) setLoading(false)
			}
		})()
		return () => {
			cancelled = true
		}
	}, [])

	return (
		<div className='min-h-screen bg-gray-50 dark:bg-gray-950 p-6'>
			<div className='max-w-5xl mx-auto'>
				<div className='flex items-center justify-between mb-6'>
					<h1 className='text-2xl font-semibold text-gray-900 dark:text-gray-100'>
						OCR 任務後台
					</h1>
					<Link
						to='/'
						className='text-sm text-primary underline underline-offset-4'>
						返回 OCR 首頁
					</Link>
				</div>
				<p className='text-sm text-gray-600 dark:text-gray-400 mb-4'>
					手機 App 與網頁上傳的任務會出現在此；完成後可匯出 Excel。
				</p>

				{loading && <p className='text-gray-500'>載入中…</p>}
				{err && (
					<p className='text-red-600 dark:text-red-400 text-sm'>{err}</p>
				)}

				{!loading && !err && (
					<div className='overflow-x-auto rounded-lg border border-gray-200 dark:border-gray-800 bg-white dark:bg-gray-900'>
						<table className='w-full text-sm'>
							<thead>
								<tr className='border-b border-gray-200 dark:border-gray-800 bg-gray-100 dark:bg-gray-800'>
									<th className='text-left p-3 font-medium'>檔名</th>
									<th className='text-left p-3 font-medium'>狀態</th>
									<th className='text-left p-3 font-medium'>建立時間</th>
									<th className='text-left p-3 font-medium'>task_id</th>
									<th className='text-left p-3 font-medium'>操作</th>
								</tr>
							</thead>
							<tbody>
								{tasks.map(t => (
									<tr
										key={t.task_id}
										className='border-b border-gray-100 dark:border-gray-800'>
										<td className='p-3 max-w-[200px] truncate'>
											{t.original_filename ?? '—'}
										</td>
										<td className='p-3'>{t.status}</td>
										<td className='p-3 whitespace-nowrap text-gray-600 dark:text-gray-400'>
											{t.created_at
												? new Date(t.created_at).toLocaleString()
												: '—'}
										</td>
										<td className='p-3 font-mono text-xs text-gray-500'>
											{t.task_id.slice(0, 8)}…
										</td>
										<td className='p-3'>
											{t.status === 'completed' ? (
												<a
													href={getTaskExportXlsxUrl(t.task_id)}
													className='text-primary underline underline-offset-2'
													download>
													匯出 Excel
												</a>
											) : (
												<span className='text-gray-400'>—</span>
											)}
										</td>
									</tr>
								))}
							</tbody>
						</table>
						{tasks.length === 0 && (
							<p className='p-6 text-center text-gray-500'>尚無任務</p>
						)}
					</div>
				)}
			</div>
		</div>
	)
}
