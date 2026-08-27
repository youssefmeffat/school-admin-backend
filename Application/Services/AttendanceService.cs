using Application.Common;
using Application.DTOs;
using Core.Enums;
using Core.Models;
using Infrastructure.Data;
using Microsoft.EntityFrameworkCore;

namespace Application.Services
{
    public class AttendanceService : IAttendanceService
    {
        private readonly SchoolDbContext _db;

        public AttendanceService(SchoolDbContext db)
        {
            _db = db;
        }

        // =====================================================
        // STUDENT ATTENDANCE
        // =====================================================

        public async Task<List<StudentAttendanceListItemDto>> GetStudentAttendanceAsync(
            DateTime date, int? studentId, int? gradeId, int? classId)
        {
            var attendanceDate = date.Date;

            var query = _db.Students
                .AsNoTracking()
                .Include(s => s.Class)
                .ThenInclude(c => c!.Grade)
                .AsQueryable();

            if (studentId.HasValue)
                query = query.Where(s => s.Id == studentId.Value);

            if (gradeId.HasValue)
                query = query.Where(s => s.Class != null && s.Class.GradeId == gradeId.Value);

            if (classId.HasValue)
                query = query.Where(s => s.ClassId == classId.Value);

            return await query
                .OrderBy(s => s.FullName)
                .Select(s => new StudentAttendanceListItemDto
                {
                    StudentId = s.Id,
                    StudentName = s.FullName,
                    StudentCode = s.Code,

                    ClassId = s.ClassId,
                    ClassName = s.Class != null ? s.Class.Name : null,
                    GradeId = s.Class != null ? s.Class.GradeId : (int?)null,
                    GradeName = s.Class != null && s.Class.Grade != null ? s.Class.Grade.Name : null,

                    Attendance = _db.StudentAttendances
                        .Where(a => a.StudentId == s.Id && a.Date == attendanceDate)
                        .Select(a => new AttendanceRecordDto
                        {
                            Id = a.Id,
                            Status = a.Status,
                            CheckInTime = a.CheckInTime,
                            CheckOutTime = a.CheckOutTime,
                            Notes = a.Notes,
                            RecordedBy = a.RecordedBy,
                            CreatedAt = a.CreatedAt,
                            UpdatedAt = a.UpdatedAt
                        })
                        .FirstOrDefault()
                })
                .ToListAsync();
        }

        public async Task<ServiceResult<List<StudentAttendanceHistoryDto>>> GetStudentHistoryAsync(
            int studentId, DateTime? from, DateTime? to)
        {
            var studentExists = await _db.Students.AnyAsync(s => s.Id == studentId);
            if (!studentExists)
                return ServiceResult<List<StudentAttendanceHistoryDto>>.Fail(
                    ServiceError.NotFound(new { message = "Student not found." }));

            var query = _db.StudentAttendances
                .AsNoTracking()
                .Where(a => a.StudentId == studentId);

            if (from.HasValue)
            {
                var fromDate = from.Value.Date;
                query = query.Where(a => a.Date >= fromDate);
            }

            if (to.HasValue)
            {
                var toDate = to.Value.Date;
                query = query.Where(a => a.Date <= toDate);
            }

            var attendance = await query
                .OrderByDescending(a => a.Date)
                .Select(a => new StudentAttendanceHistoryDto
                {
                    Id = a.Id,
                    StudentId = a.StudentId,
                    Date = a.Date,
                    Status = a.Status,
                    CheckInTime = a.CheckInTime,
                    CheckOutTime = a.CheckOutTime,
                    Notes = a.Notes,
                    RecordedBy = a.RecordedBy,
                    CreatedAt = a.CreatedAt,
                    UpdatedAt = a.UpdatedAt
                })
                .ToListAsync();

            return ServiceResult<List<StudentAttendanceHistoryDto>>.Success(attendance);
        }

        public async Task<ServiceResult<SavedStudentAttendanceDto>> SaveStudentAttendanceAsync(
            StudentAttendanceRequest request)
        {
            var studentExists = await _db.Students.AnyAsync(s => s.Id == request.StudentId);
            if (!studentExists)
                return ServiceResult<SavedStudentAttendanceDto>.Fail(
                    ServiceError.Validation(new { message = "Student not found." }));

            var date = request.Date.Date;

            var attendance = await _db.StudentAttendances
                .FirstOrDefaultAsync(a => a.StudentId == request.StudentId && a.Date == date);

            var now = DateTime.UtcNow;

            if (attendance == null)
            {
                attendance = new StudentAttendance
                {
                    StudentId = request.StudentId,
                    Date = date,
                    Status = request.Status,
                    CheckInTime = request.CheckInTime,
                    CheckOutTime = request.CheckOutTime,
                    Notes = request.Notes,
                    RecordedBy = request.RecordedBy,
                    CreatedAt = now,
                    UpdatedAt = now
                };

                _db.StudentAttendances.Add(attendance);
            }
            else
            {
                attendance.Status = request.Status;
                attendance.CheckInTime = request.CheckInTime;
                attendance.CheckOutTime = request.CheckOutTime;
                attendance.Notes = request.Notes;
                attendance.RecordedBy = request.RecordedBy;
                attendance.UpdatedAt = now;
            }

            await _db.SaveChangesAsync();

            return ServiceResult<SavedStudentAttendanceDto>.Success(new SavedStudentAttendanceDto
            {
                Id = attendance.Id,
                StudentId = attendance.StudentId,
                Date = attendance.Date,
                Status = attendance.Status,
                CheckInTime = attendance.CheckInTime,
                CheckOutTime = attendance.CheckOutTime,
                Notes = attendance.Notes,
                RecordedBy = attendance.RecordedBy,
                CreatedAt = attendance.CreatedAt,
                UpdatedAt = attendance.UpdatedAt
            });
        }

        public async Task<GenerateAttendanceResultDto> GenerateStudentAttendanceAsync(
            GenerateAttendanceRequest request)
        {
            var date = request.Date.Date;

            var studentsQuery = _db.Students.AsQueryable();

            if (request.GradeId.HasValue)
                studentsQuery = studentsQuery.Where(s => s.Class != null && s.Class.GradeId == request.GradeId.Value);

            if (request.ClassId.HasValue)
                studentsQuery = studentsQuery.Where(s => s.ClassId == request.ClassId.Value);

            var studentIds = await studentsQuery.Select(s => s.Id).ToListAsync();

            var existingStudentIds = await _db.StudentAttendances
                .Where(a => a.Date == date && studentIds.Contains(a.StudentId))
                .Select(a => a.StudentId)
                .ToListAsync();

            var existingSet = existingStudentIds.ToHashSet();

            var now = DateTime.UtcNow;

            var records = studentIds
                .Where(id => !existingSet.Contains(id))
                .Select(id => new StudentAttendance
                {
                    StudentId = id,
                    Date = date,
                    Status = AttendanceStatus.Unrecorded,
                    CreatedAt = now,
                    UpdatedAt = now
                })
                .ToList();

            if (records.Count > 0)
            {
                _db.StudentAttendances.AddRange(records);
                await _db.SaveChangesAsync();
            }

            return new GenerateAttendanceResultDto
            {
                Date = date,
                Created = records.Count,
                Existing = existingStudentIds.Count
            };
        }

        public async Task<ServiceResult<BulkAttendanceSaveResultDto>> SaveStudentAttendanceBulkAsync(
            BulkStudentAttendanceRequest request)
        {
            var date = request.Date.Date;

            var studentIds = request.Records.Select(r => r.StudentId).Distinct().ToList();

            var existingStudentIds = await _db.Students
                .Where(s => studentIds.Contains(s.Id))
                .Select(s => s.Id)
                .ToListAsync();

            var missingStudentIds = studentIds.Except(existingStudentIds).ToList();

            if (missingStudentIds.Count > 0)
                return ServiceResult<BulkAttendanceSaveResultDto>.Fail(
                    ServiceError.Validation(new
                    {
                        message = "One or more students do not exist.",
                        studentIds = missingStudentIds
                    }));

            var existing = await _db.StudentAttendances
                .Where(a => a.Date == date && studentIds.Contains(a.StudentId))
                .ToDictionaryAsync(a => a.StudentId);

            var now = DateTime.UtcNow;

            foreach (var record in request.Records)
            {
                if (existing.TryGetValue(record.StudentId, out var attendance))
                {
                    attendance.Status = record.Status;
                    attendance.CheckInTime = record.CheckInTime;
                    attendance.CheckOutTime = record.CheckOutTime;
                    attendance.Notes = record.Notes;
                    attendance.RecordedBy = record.RecordedBy;
                    attendance.UpdatedAt = now;
                }
                else
                {
                    attendance = new StudentAttendance
                    {
                        StudentId = record.StudentId,
                        Date = date,
                        Status = record.Status,
                        CheckInTime = record.CheckInTime,
                        CheckOutTime = record.CheckOutTime,
                        Notes = record.Notes,
                        RecordedBy = record.RecordedBy,
                        CreatedAt = now,
                        UpdatedAt = now
                    };

                    _db.StudentAttendances.Add(attendance);
                }
            }

            await _db.SaveChangesAsync();

            return ServiceResult<BulkAttendanceSaveResultDto>.Success(new BulkAttendanceSaveResultDto
            {
                Message = "Student attendance saved successfully.",
                Date = date
            });
        }

        // =====================================================
        // TEACHER ATTENDANCE
        // =====================================================

        public async Task<List<TeacherAttendanceListItemDto>> GetTeacherAttendanceAsync(
            DateTime date, int? teacherId)
        {
            var attendanceDate = date.Date;

            var query = _db.Teachers.AsNoTracking().AsQueryable();

            if (teacherId.HasValue)
                query = query.Where(t => t.Id == teacherId.Value);

            return await query
                .OrderBy(t => t.FullName)
                .Select(t => new TeacherAttendanceListItemDto
                {
                    TeacherId = t.Id,
                    TeacherName = t.FullName,
                    TeacherCode = t.Code,
                    Phone = t.Phone,
                    Email = t.Email,

                    Attendance = _db.TeacherAttendances
                        .Where(a => a.TeacherId == t.Id && a.Date == attendanceDate)
                        .Select(a => new AttendanceRecordDto
                        {
                            Id = a.Id,
                            Status = a.Status,
                            CheckInTime = a.CheckInTime,
                            CheckOutTime = a.CheckOutTime,
                            Notes = a.Notes,
                            RecordedBy = a.RecordedBy,
                            CreatedAt = a.CreatedAt,
                            UpdatedAt = a.UpdatedAt
                        })
                        .FirstOrDefault()
                })
                .ToListAsync();
        }

        public async Task<ServiceResult<List<TeacherAttendanceHistoryDto>>> GetTeacherHistoryAsync(
            int teacherId, DateTime? from, DateTime? to)
        {
            var teacherExists = await _db.Teachers.AnyAsync(t => t.Id == teacherId);
            if (!teacherExists)
                return ServiceResult<List<TeacherAttendanceHistoryDto>>.Fail(
                    ServiceError.NotFound(new { message = "Teacher not found." }));

            var query = _db.TeacherAttendances
                .AsNoTracking()
                .Where(a => a.TeacherId == teacherId);

            if (from.HasValue)
            {
                var fromDate = from.Value.Date;
                query = query.Where(a => a.Date >= fromDate);
            }

            if (to.HasValue)
            {
                var toDate = to.Value.Date;
                query = query.Where(a => a.Date <= toDate);
            }

            var attendance = await query
                .OrderByDescending(a => a.Date)
                .Select(a => new TeacherAttendanceHistoryDto
                {
                    Id = a.Id,
                    TeacherId = a.TeacherId,
                    Date = a.Date,
                    Status = a.Status,
                    CheckInTime = a.CheckInTime,
                    CheckOutTime = a.CheckOutTime,
                    Notes = a.Notes,
                    RecordedBy = a.RecordedBy,
                    CreatedAt = a.CreatedAt,
                    UpdatedAt = a.UpdatedAt
                })
                .ToListAsync();

            return ServiceResult<List<TeacherAttendanceHistoryDto>>.Success(attendance);
        }

        public async Task<ServiceResult<SavedTeacherAttendanceDto>> SaveTeacherAttendanceAsync(
            TeacherAttendanceRequest request)
        {
            var teacherExists = await _db.Teachers.AnyAsync(t => t.Id == request.TeacherId);
            if (!teacherExists)
                return ServiceResult<SavedTeacherAttendanceDto>.Fail(
                    ServiceError.Validation(new { message = "Teacher not found." }));

            var date = request.Date.Date;

            var attendance = await _db.TeacherAttendances
                .FirstOrDefaultAsync(a => a.TeacherId == request.TeacherId && a.Date == date);

            var now = DateTime.UtcNow;

            if (attendance == null)
            {
                attendance = new TeacherAttendance
                {
                    TeacherId = request.TeacherId,
                    Date = date,
                    Status = request.Status,
                    CheckInTime = request.CheckInTime,
                    CheckOutTime = request.CheckOutTime,
                    Notes = request.Notes,
                    RecordedBy = request.RecordedBy,
                    CreatedAt = now,
                    UpdatedAt = now
                };

                _db.TeacherAttendances.Add(attendance);
            }
            else
            {
                attendance.Status = request.Status;
                attendance.CheckInTime = request.CheckInTime;
                attendance.CheckOutTime = request.CheckOutTime;
                attendance.Notes = request.Notes;
                attendance.RecordedBy = request.RecordedBy;
                attendance.UpdatedAt = now;
            }

            await _db.SaveChangesAsync();

            return ServiceResult<SavedTeacherAttendanceDto>.Success(new SavedTeacherAttendanceDto
            {
                Id = attendance.Id,
                TeacherId = attendance.TeacherId,
                Date = attendance.Date,
                Status = attendance.Status,
                CheckInTime = attendance.CheckInTime,
                CheckOutTime = attendance.CheckOutTime,
                Notes = attendance.Notes,
                RecordedBy = attendance.RecordedBy,
                CreatedAt = attendance.CreatedAt,
                UpdatedAt = attendance.UpdatedAt
            });
        }

        public async Task<GenerateAttendanceResultDto> GenerateTeacherAttendanceAsync(
            GenerateAttendanceRequest request)
        {
            var date = request.Date.Date;

            var teacherIds = await _db.Teachers.Select(t => t.Id).ToListAsync();

            var existingTeacherIds = await _db.TeacherAttendances
                .Where(a => a.Date == date && teacherIds.Contains(a.TeacherId))
                .Select(a => a.TeacherId)
                .ToListAsync();

            var existingSet = existingTeacherIds.ToHashSet();

            var now = DateTime.UtcNow;

            var records = teacherIds
                .Where(id => !existingSet.Contains(id))
                .Select(id => new TeacherAttendance
                {
                    TeacherId = id,
                    Date = date,
                    Status = AttendanceStatus.Unrecorded,
                    CreatedAt = now,
                    UpdatedAt = now
                })
                .ToList();

            if (records.Count > 0)
            {
                _db.TeacherAttendances.AddRange(records);
                await _db.SaveChangesAsync();
            }

            return new GenerateAttendanceResultDto
            {
                Date = date,
                Created = records.Count,
                Existing = existingTeacherIds.Count
            };
        }

        public async Task<ServiceResult<BulkAttendanceSaveResultDto>> SaveTeacherAttendanceBulkAsync(
            BulkTeacherAttendanceRequest request)
        {
            var date = request.Date.Date;

            var teacherIds = request.Records.Select(r => r.TeacherId).Distinct().ToList();

            var existingTeacherIds = await _db.Teachers
                .Where(t => teacherIds.Contains(t.Id))
                .Select(t => t.Id)
                .ToListAsync();

            var missingTeacherIds = teacherIds.Except(existingTeacherIds).ToList();

            if (missingTeacherIds.Count > 0)
                return ServiceResult<BulkAttendanceSaveResultDto>.Fail(
                    ServiceError.Validation(new
                    {
                        message = "One or more teachers do not exist.",
                        teacherIds = missingTeacherIds
                    }));

            var existing = await _db.TeacherAttendances
                .Where(a => a.Date == date && teacherIds.Contains(a.TeacherId))
                .ToDictionaryAsync(a => a.TeacherId);

            var now = DateTime.UtcNow;

            foreach (var record in request.Records)
            {
                if (existing.TryGetValue(record.TeacherId, out var attendance))
                {
                    attendance.Status = record.Status;
                    attendance.CheckInTime = record.CheckInTime;
                    attendance.CheckOutTime = record.CheckOutTime;
                    attendance.Notes = record.Notes;
                    attendance.RecordedBy = record.RecordedBy;
                    attendance.UpdatedAt = now;
                }
                else
                {
                    attendance = new TeacherAttendance
                    {
                        TeacherId = record.TeacherId,
                        Date = date,
                        Status = record.Status,
                        CheckInTime = record.CheckInTime,
                        CheckOutTime = record.CheckOutTime,
                        Notes = record.Notes,
                        RecordedBy = record.RecordedBy,
                        CreatedAt = now,
                        UpdatedAt = now
                    };

                    _db.TeacherAttendances.Add(attendance);
                }
            }

            await _db.SaveChangesAsync();

            return ServiceResult<BulkAttendanceSaveResultDto>.Success(new BulkAttendanceSaveResultDto
            {
                Message = "Teacher attendance saved successfully.",
                Date = date
            });
        }
    }
}
