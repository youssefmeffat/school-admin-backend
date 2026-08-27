using System.ComponentModel.DataAnnotations;
using System.Collections.Generic;

namespace Application.DTOs
{
    public class TeacherCreateDto
    {
        [Required]
        public string? FullName { get; set; }
        public string? Email { get; set; }
        public string? Code { get; set; }
    }

    public class TeacherDto
    {
        public int Id { get; set; }
        public string? FullName { get; set; }
        public string? Email { get; set; }
    }
}
