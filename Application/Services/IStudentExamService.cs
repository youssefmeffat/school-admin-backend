using Core.Models;
using System.Collections.Generic;
using System.Threading.Tasks;
using Application.DTOs;

namespace Application.Services
{
    public interface IStudentExamService
    {
        Task EnrollStudentAsync(int studentId, int examId);
        Task SetScoreAsync(int studentId, int examId, decimal score);
        Task<List<ExamParticipantDto>> GetExamParticipantsAsync(int examId);
        Task<List<StudentExamResult>> GetStudentExamsAsync(int studentId);
    }
}
