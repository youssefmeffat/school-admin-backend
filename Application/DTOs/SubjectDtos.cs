using System.ComponentModel.DataAnnotations;

namespace Application.DTOs
{
    public class SubjectCreateDto
    {
        [Required]
        public string? Name { get; set; }
    }

    public class SubjectDto
    {
        public int Id { get; set; }
        public string? Name { get; set; }
    }
}
