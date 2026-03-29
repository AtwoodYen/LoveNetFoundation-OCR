import { FileUpload, type TaskResponse, type UploadedFile } from './FileUpload'
import { FilePreview } from './FilePreview'
import { OCRResults } from './OCRResults'
import { Link } from '@tanstack/react-router'
import { useState } from 'react'

export function OCRPage() {
	const [uploadFile, setUploadFile] = useState<UploadedFile | null>(null)
	const [parsedResult, setParsedResult] = useState<TaskResponse | null>(null)




	return (
		<div className='h-screen flex overflow-hidden bg-gray-50 dark:bg-gray-950'>
			<div className='absolute top-2 right-4 z-10'>
				<Link
					to='/admin'
					className='text-xs text-gray-500 hover:text-primary underline underline-offset-2'>
					任務後台 · Excel
				</Link>
			</div>
			{/* 左侧栏 - 文件上传 */}
			<div className='w-60 shrink-0'>
				<FileUpload
					onFileUploaded={file => {
						setUploadFile(file)
					}}
					onTaskStatusChange={data => {
						setParsedResult(data)
					}}
				/>
			</div>

			<main className='h-screen flex-1 min-w-0 grid grid-cols-2 overflow-hidden'>
				<FilePreview file={uploadFile} result={parsedResult} />
				<OCRResults result={parsedResult} fileName={uploadFile?.name} />
			</main>
		</div>
	)
}
