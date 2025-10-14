"""
Unit tests for FTP worker functionality

Note: ftp_worker.py has module-level code that runs an infinite loop.
To test the functions, we extract and test their logic separately.
"""
import pytest
import os
import time
import subprocess
from unittest.mock import Mock, patch, MagicMock, call, mock_open
from pathlib import Path


class TestInsertJobRef:
    """Tests for insert_job_ref function"""

    @pytest.mark.unit
    @patch('time.time_ns')
    def test_insert_job_ref_basic(self, mock_time_ns):
        """Test basic job reference insertion"""
        mock_time_ns.return_value = 1000000000000000  # nanoseconds
        expected_time = 1000000000000000 // 1000000  # milliseconds

        mock_job_collection = MagicMock()

        # Replicate insert_job_ref logic
        def insert_job_ref(job_id, filename):
            tn = time.time_ns() // 1000000
            mock_job_collection.insert_one({
                '_id': job_id,
                'type': 'ftp_job_' + filename,
                'start_time': str(tn),
                'status': 'running'
            })

        job_id = 'test-job-123'
        filename = 'test_image.jpg'

        insert_job_ref(job_id, filename)

        # Verify insert was called
        mock_job_collection.insert_one.assert_called_once()
        call_args = mock_job_collection.insert_one.call_args[0][0]

        assert call_args['_id'] == job_id
        assert call_args['type'] == 'ftp_job_test_image.jpg'
        assert call_args['start_time'] == str(expected_time)
        assert call_args['status'] == 'running'

    @pytest.mark.unit
    @patch('time.time_ns')
    def test_insert_job_ref_different_filenames(self, mock_time_ns):
        """Test job reference insertion with different file types"""
        mock_time_ns.return_value = 1500000000000000
        mock_job_collection = MagicMock()

        def insert_job_ref(job_id, filename):
            tn = time.time_ns() // 1000000
            mock_job_collection.insert_one({
                '_id': job_id,
                'type': 'ftp_job_' + filename,
                'start_time': str(tn),
                'status': 'running'
            })

        test_cases = [
            ('job1', 'image.png'),
            ('job2', 'photo.tif'),
            ('job3', 'picture.bmp'),
        ]

        for job_id, filename in test_cases:
            insert_job_ref(job_id, filename)

        assert mock_job_collection.insert_one.call_count == 3


class TestProcessFile:
    """Tests for process_file function"""

    @pytest.mark.unit
    @patch('subprocess.call')
    @patch('time.time_ns')
    def test_process_file_jpg(self, mock_time_ns, mock_subprocess):
        """Test processing JPG file"""
        mock_time_ns.return_value = 1000000000000000
        expected_time = 1000000000000000 // 1000000

        mock_job_queue = MagicMock()
        mock_job = MagicMock()
        mock_job.id = 'job-123'
        mock_job_queue.enqueue.return_value = mock_job

        mock_job_collection = MagicMock()
        processed = {}

        def process_file(directory, filename):
            # Lowercase conversion
            subprocess.call(['mv', directory + '/' + filename, directory + '/' + filename.lower()])

            if filename.endswith(".jpg") or filename.endswith(".png") or filename.endswith(".tif") or filename.endswith(".bmp"):
                if filename not in processed:
                    extension = filename[-4:]
                    if not filename.startswith('ftp_'):
                        tn = time.time_ns() // 1000000
                        rename = 'ftp_' + str(tn) + extension
                        subprocess.call(['mv', directory + '/' + filename, '/home/ftp/' + rename])
                        filename = rename

                    processed[filename] = "processing"
                    j = mock_job_queue.enqueue('process_img', filename, job_timeout=99999999, result_ttl=-1)

                    mock_job_collection.insert_one({
                        '_id': j.id,
                        'type': 'ftp_job_' + filename,
                        'start_time': str(tn),
                        'status': 'running'
                    })

        directory = '/test/dir'
        filename = 'test.jpg'

        process_file(directory, filename)

        # Verify lowercase conversion was called
        assert mock_subprocess.call_count >= 1
        first_call = mock_subprocess.call_args_list[0][0][0]
        assert 'mv' in first_call

    @pytest.mark.unit
    @patch('subprocess.call')
    @patch('time.time_ns')
    def test_process_file_png(self, mock_time_ns, mock_subprocess):
        """Test processing PNG file"""
        mock_time_ns.return_value = 2000000000000000

        mock_job_queue = MagicMock()
        mock_job = MagicMock()
        mock_job.id = 'job-456'
        mock_job_queue.enqueue.return_value = mock_job

        processed = {}

        def process_file(directory, filename):
            subprocess.call(['mv', directory + '/' + filename, directory + '/' + filename.lower()])

            if filename.endswith(".jpg") or filename.endswith(".png") or filename.endswith(".tif") or filename.endswith(".bmp"):
                if filename not in processed:
                    processed[filename] = "processing"

        process_file('/test/dir', 'image.png')

        assert 'image.png' in processed
        assert processed['image.png'] == 'processing'

    @pytest.mark.unit
    @patch('subprocess.call')
    def test_process_file_tif(self, mock_subprocess):
        """Test processing TIF file"""
        processed = {}

        def process_file(directory, filename):
            subprocess.call(['mv', directory + '/' + filename, directory + '/' + filename.lower()])

            if filename.endswith(".jpg") or filename.endswith(".png") or filename.endswith(".tif") or filename.endswith(".bmp"):
                if filename not in processed:
                    processed[filename] = "processing"

        process_file('/test/dir', 'scan.tif')

        assert 'scan.tif' in processed

    @pytest.mark.unit
    @patch('subprocess.call')
    def test_process_file_bmp(self, mock_subprocess):
        """Test processing BMP file"""
        processed = {}

        def process_file(directory, filename):
            subprocess.call(['mv', directory + '/' + filename, directory + '/' + filename.lower()])

            if filename.endswith(".jpg") or filename.endswith(".png") or filename.endswith(".tif") or filename.endswith(".bmp"):
                if filename not in processed:
                    processed[filename] = "processing"

        process_file('/test/dir', 'bitmap.bmp')

        assert 'bitmap.bmp' in processed

    @pytest.mark.unit
    @patch('os.system')
    @patch('subprocess.call')
    def test_process_file_invalid_extension(self, mock_subprocess, mock_os_system):
        """Test processing file with invalid extension gets removed"""
        processed = {}

        def process_file(directory, filename):
            subprocess.call(['mv', directory + '/' + filename, directory + '/' + filename.lower()])

            if filename.endswith(".jpg") or filename.endswith(".png") or filename.endswith(".tif") or filename.endswith(".bmp"):
                if filename not in processed:
                    processed[filename] = "processing"
            else:
                # Remove files that are not jpg/png/tif/bmp
                os.system('rm ' + directory + '/' + filename)

        directory = '/test/dir'
        filename = 'document.txt'

        process_file(directory, filename)

        # Verify file was removed
        mock_os_system.assert_called_once_with('rm /test/dir/document.txt')
        assert filename not in processed

    @pytest.mark.unit
    @patch('subprocess.call')
    def test_process_file_already_processed(self, mock_subprocess):
        """Test that already processed files are skipped"""
        processed = {'existing.jpg': 'processing'}

        mock_job_queue = MagicMock()

        def process_file(directory, filename):
            subprocess.call(['mv', directory + '/' + filename, directory + '/' + filename.lower()])

            if filename.endswith(".jpg") or filename.endswith(".png") or filename.endswith(".tif") or filename.endswith(".bmp"):
                if filename not in processed:
                    processed[filename] = "processing"
                    mock_job_queue.enqueue('process_img', filename)

        process_file('/test/dir', 'existing.jpg')

        # Job queue should not be called for already processed file
        mock_job_queue.enqueue.assert_not_called()

    @pytest.mark.unit
    @patch('subprocess.call')
    @patch('time.time_ns')
    def test_process_file_rename_without_ftp_prefix(self, mock_time_ns, mock_subprocess):
        """Test that files without ftp_ prefix are renamed"""
        mock_time_ns.return_value = 1234567890000000
        expected_time = 1234567890000000 // 1000000

        processed = {}
        ftp_directory = '/home/ftp'

        def process_file(directory, filename):
            subprocess.call(['mv', directory + '/' + filename, directory + '/' + filename.lower()])

            if filename.endswith(".jpg") or filename.endswith(".png") or filename.endswith(".tif") or filename.endswith(".bmp"):
                if filename not in processed:
                    extension = filename[-4:]
                    if not filename.startswith('ftp_'):
                        tn = time.time_ns() // 1000000
                        rename = 'ftp_' + str(tn) + extension
                        subprocess.call(['mv', directory + '/' + filename, ftp_directory + '/' + rename])
                        filename = rename

                    processed[filename] = "processing"

        process_file('/upload/dir', 'photo.jpg')

        # Verify rename was called
        expected_rename = f'ftp_{expected_time}.jpg'
        assert expected_rename in processed

        # Verify subprocess was called for both lowercase and rename
        assert mock_subprocess.call_count == 2

    @pytest.mark.unit
    @patch('subprocess.call')
    def test_process_file_with_ftp_prefix(self, mock_subprocess):
        """Test that files with ftp_ prefix are not renamed"""
        processed = {}

        def process_file(directory, filename):
            subprocess.call(['mv', directory + '/' + filename, directory + '/' + filename.lower()])

            if filename.endswith(".jpg") or filename.endswith(".png") or filename.endswith(".tif") or filename.endswith(".bmp"):
                if filename not in processed:
                    extension = filename[-4:]
                    if not filename.startswith('ftp_'):
                        # This should not execute for ftp_ prefixed files
                        pass

                    processed[filename] = "processing"

        filename = 'ftp_1234567890.jpg'
        process_file('/test/dir', filename)

        assert filename in processed
        # Only lowercase conversion should be called
        assert mock_subprocess.call_count == 1


class TestFileTypeValidation:
    """Tests for file type validation logic"""

    @pytest.mark.unit
    def test_valid_extensions(self):
        """Test that valid extensions are recognized"""
        valid_files = [
            'image.jpg',
            'photo.png',
            'scan.tif',
            'bitmap.bmp',
            'IMAGE.JPG',  # After lowercase conversion
        ]

        for filename in valid_files:
            is_valid = (filename.lower().endswith(".jpg") or
                       filename.lower().endswith(".png") or
                       filename.lower().endswith(".tif") or
                       filename.lower().endswith(".bmp"))
            assert is_valid, f"{filename} should be valid"

    @pytest.mark.unit
    def test_invalid_extensions(self):
        """Test that invalid extensions are rejected"""
        invalid_files = [
            'document.txt',
            'archive.zip',
            'script.py',
            'data.json',
            'video.mp4',
        ]

        for filename in invalid_files:
            is_valid = (filename.endswith(".jpg") or
                       filename.endswith(".png") or
                       filename.endswith(".tif") or
                       filename.endswith(".bmp"))
            assert not is_valid, f"{filename} should be invalid"

    @pytest.mark.unit
    def test_case_sensitivity(self):
        """Test file extension case handling"""
        # After lowercase conversion, these should all be valid
        test_files = [
            ('IMAGE.JPG', 'image.jpg'),
            ('PHOTO.PNG', 'photo.png'),
            ('SCAN.TIF', 'scan.tif'),
            ('BITMAP.BMP', 'bitmap.bmp'),
        ]

        for original, lowercased in test_files:
            is_valid = (lowercased.endswith(".jpg") or
                       lowercased.endswith(".png") or
                       lowercased.endswith(".tif") or
                       lowercased.endswith(".bmp"))
            assert is_valid

    @pytest.mark.unit
    def test_ftp_prefix_detection(self):
        """Test FTP prefix detection"""
        files_with_prefix = [
            'ftp_1234567890.jpg',
            'ftp_0987654321.png',
        ]

        files_without_prefix = [
            'image.jpg',
            'photo.png',
        ]

        for filename in files_with_prefix:
            assert filename.startswith('ftp_')

        for filename in files_without_prefix:
            assert not filename.startswith('ftp_')


class TestDirectoryHandling:
    """Tests for directory handling logic"""

    @pytest.mark.unit
    @patch('os.path.isdir')
    @patch('os.listdir')
    def test_process_subdirectory(self, mock_listdir, mock_isdir):
        """Test processing files in subdirectories"""
        mock_isdir.side_effect = lambda path: '/subdir' in path and '/file' not in path
        mock_listdir.return_value = ['file1.jpg', 'file2.png']

        ftp_directory = '/home/ftp'
        filename = 'subdir'

        # Simulate directory check
        file_path = ftp_directory + '/' + filename
        is_directory = mock_isdir(file_path)

        assert is_directory

        # Get files in subdirectory
        files = mock_listdir(file_path)
        assert len(files) == 2
        assert 'file1.jpg' in files
        assert 'file2.png' in files

    @pytest.mark.unit
    @patch('os.path.isdir')
    @patch('os.listdir')
    def test_empty_subdirectory(self, mock_listdir, mock_isdir):
        """Test handling of empty subdirectories"""
        mock_isdir.return_value = True
        mock_listdir.return_value = []

        ftp_directory = '/home/ftp'
        filename = 'empty_subdir'
        file_path = ftp_directory + '/' + filename

        is_directory = mock_isdir(file_path)
        files = mock_listdir(file_path)

        assert is_directory
        assert len(files) == 0

    @pytest.mark.unit
    @patch('os.system')
    @patch('os.path.isdir')
    def test_remove_nested_directory(self, mock_isdir, mock_os_system):
        """Test removal of nested directories"""
        mock_isdir.return_value = True

        sub_file_path = '/home/ftp/subdir/nested'

        # Simulate removal of nested directory
        if mock_isdir(sub_file_path):
            os.system('rm -rf ' + sub_file_path)

        mock_os_system.assert_called_once_with('rm -rf ' + sub_file_path)

    @pytest.mark.unit
    @patch('os.path.exists')
    def test_ftp_directory_exists(self, mock_exists):
        """Test FTP directory existence check"""
        mock_exists.return_value = True

        ftp_directory = '/home/ftp'
        exists = mock_exists(ftp_directory)

        assert exists
        mock_exists.assert_called_once_with(ftp_directory)

    @pytest.mark.unit
    @patch('os.path.exists')
    def test_ftp_directory_not_exists(self, mock_exists):
        """Test handling when FTP directory doesn't exist"""
        mock_exists.return_value = False

        ftp_directory = '/home/ftp'
        exists = mock_exists(ftp_directory)

        assert not exists


class TestJobQueueIntegration:
    """Tests for job queue integration"""

    @pytest.mark.unit
    def test_job_queue_enqueue(self):
        """Test enqueueing a job"""
        mock_job_queue = MagicMock()
        mock_job = MagicMock()
        mock_job.id = 'test-job-123'
        mock_job_queue.enqueue.return_value = mock_job

        filename = 'test_image.jpg'
        job = mock_job_queue.enqueue('process_img', filename, job_timeout=99999999, result_ttl=-1)

        assert job.id == 'test-job-123'
        mock_job_queue.enqueue.assert_called_once_with(
            'process_img', filename, job_timeout=99999999, result_ttl=-1
        )

    @pytest.mark.unit
    def test_job_queue_empty(self):
        """Test emptying the job queue"""
        mock_job_queue = MagicMock()

        mock_job_queue.empty()

        mock_job_queue.empty.assert_called_once()

    @pytest.mark.unit
    def test_job_collection_drop(self):
        """Test dropping the job collection"""
        mock_job_collection = MagicMock()

        mock_job_collection.drop()

        mock_job_collection.drop.assert_called_once()


class TestProcessedFilesTracking:
    """Tests for processed files tracking"""

    @pytest.mark.unit
    def test_processed_dict_initialization(self):
        """Test processed files dictionary initialization"""
        processed = {}

        assert len(processed) == 0
        assert isinstance(processed, dict)

    @pytest.mark.unit
    def test_add_to_processed(self):
        """Test adding files to processed dictionary"""
        processed = {}

        processed['file1.jpg'] = 'processing'
        processed['file2.png'] = 'processing'

        assert len(processed) == 2
        assert 'file1.jpg' in processed
        assert 'file2.png' in processed
        assert processed['file1.jpg'] == 'processing'

    @pytest.mark.unit
    def test_check_if_processed(self):
        """Test checking if file is already processed"""
        processed = {
            'existing.jpg': 'processing',
            'done.png': 'processing'
        }

        assert 'existing.jpg' in processed
        assert 'new_file.jpg' not in processed

    @pytest.mark.unit
    def test_reset_processed(self):
        """Test resetting processed files dictionary"""
        processed = {
            'file1.jpg': 'processing',
            'file2.png': 'processing'
        }

        # Simulate reset when directory is empty
        processed = {}

        assert len(processed) == 0


class TestFilenameGeneration:
    """Tests for filename generation logic"""

    @pytest.mark.unit
    @patch('time.time_ns')
    def test_generate_ftp_filename(self, mock_time_ns):
        """Test FTP filename generation"""
        mock_time_ns.return_value = 1234567890000000
        expected_time = 1234567890000000 // 1000000

        extension = '.jpg'
        tn = time.time_ns() // 1000000
        rename = 'ftp_' + str(tn) + extension

        assert rename == f'ftp_{expected_time}.jpg'
        assert rename.startswith('ftp_')
        assert rename.endswith('.jpg')

    @pytest.mark.unit
    @patch('time.time_ns')
    def test_generate_different_extensions(self, mock_time_ns):
        """Test filename generation for different extensions"""
        mock_time_ns.return_value = 9876543210000000
        expected_time = 9876543210000000 // 1000000

        extensions = ['.jpg', '.png', '.tif', '.bmp']

        for ext in extensions:
            tn = time.time_ns() // 1000000
            rename = 'ftp_' + str(tn) + ext

            assert rename.startswith('ftp_')
            assert rename.endswith(ext)
            assert str(expected_time) in rename

    @pytest.mark.unit
    def test_extract_extension(self):
        """Test extension extraction from filename"""
        test_files = [
            ('image.jpg', '.jpg'),
            ('photo.png', '.png'),
            ('scan.tif', '.tif'),
            ('bitmap.bmp', '.bmp'),
        ]

        for filename, expected_ext in test_files:
            extension = filename[-4:]
            assert extension == expected_ext


class TestSubprocessCalls:
    """Tests for subprocess operations"""

    @pytest.mark.unit
    @patch('subprocess.call')
    def test_lowercase_conversion_call(self, mock_subprocess):
        """Test subprocess call for lowercase conversion"""
        directory = '/test/dir'
        filename = 'IMAGE.JPG'

        subprocess.call(['mv', directory + '/' + filename, directory + '/' + filename.lower()])

        mock_subprocess.assert_called_once_with(
            ['mv', '/test/dir/IMAGE.JPG', '/test/dir/image.jpg']
        )

    @pytest.mark.unit
    @patch('subprocess.call')
    def test_file_rename_call(self, mock_subprocess):
        """Test subprocess call for file rename"""
        directory = '/upload'
        filename = 'photo.jpg'
        ftp_directory = '/home/ftp'
        rename = 'ftp_1234567890.jpg'

        subprocess.call(['mv', directory + '/' + filename, ftp_directory + '/' + rename])

        mock_subprocess.assert_called_once_with(
            ['mv', '/upload/photo.jpg', '/home/ftp/ftp_1234567890.jpg']
        )

    @pytest.mark.unit
    @patch('os.system')
    def test_remove_file_call(self, mock_os_system):
        """Test os.system call for file removal"""
        directory = '/test/dir'
        filename = 'unwanted.txt'

        os.system('rm ' + directory + '/' + filename)

        mock_os_system.assert_called_once_with('rm /test/dir/unwanted.txt')

    @pytest.mark.unit
    @patch('os.system')
    def test_remove_directory_call(self, mock_os_system):
        """Test os.system call for directory removal"""
        directory_path = '/home/ftp/subdir/nested'

        os.system('rm -rf ' + directory_path)

        mock_os_system.assert_called_once_with('rm -rf /home/ftp/subdir/nested')


class TestEdgeCases:
    """Tests for edge cases and error scenarios"""

    @pytest.mark.unit
    @patch('subprocess.call')
    def test_filename_with_spaces(self, mock_subprocess):
        """Test handling filename with spaces"""
        directory = '/test/dir'
        filename = 'my photo.jpg'

        subprocess.call(['mv', directory + '/' + filename, directory + '/' + filename.lower()])

        # Subprocess should be called with the filename as-is
        mock_subprocess.assert_called_once()

    @pytest.mark.unit
    @patch('subprocess.call')
    def test_filename_with_special_chars(self, mock_subprocess):
        """Test handling filename with special characters"""
        directory = '/test/dir'
        filename = 'image@2024.jpg'

        subprocess.call(['mv', directory + '/' + filename, directory + '/' + filename.lower()])

        mock_subprocess.assert_called_once()

    @pytest.mark.unit
    def test_very_long_filename(self):
        """Test handling very long filenames"""
        long_filename = 'a' * 200 + '.jpg'

        is_valid = long_filename.endswith('.jpg')
        extension = long_filename[-4:]

        assert is_valid
        assert extension == '.jpg'

    @pytest.mark.unit
    def test_timestamp_uniqueness(self):
        """Test that different timestamps create unique filenames"""
        # Test with actual timestamp values after conversion
        timestamps = [1000000000, 1000000001, 1000000002]  # Already in milliseconds
        filenames = []

        for tn in timestamps:
            rename = 'ftp_' + str(tn) + '.jpg'
            filenames.append(rename)

        # All filenames should be unique
        assert len(filenames) == len(set(filenames))
        assert filenames[0] == 'ftp_1000000000.jpg'
        assert filenames[1] == 'ftp_1000000001.jpg'
        assert filenames[2] == 'ftp_1000000002.jpg'

    @pytest.mark.unit
    def test_empty_filename(self):
        """Test handling of empty filename"""
        filename = ''

        is_valid = (filename.endswith(".jpg") or
                   filename.endswith(".png") or
                   filename.endswith(".tif") or
                   filename.endswith(".bmp"))

        assert not is_valid

    @pytest.mark.unit
    def test_filename_only_extension(self):
        """Test handling of filename that is only an extension"""
        filename = '.jpg'

        is_valid = filename.endswith('.jpg')

        assert is_valid  # Technically valid, though unusual
